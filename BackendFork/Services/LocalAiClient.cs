using System.Net.Http.Headers;
using System.Text.Json;
using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Infrastructure;
using Microsoft.Extensions.Options;
using static BackendFork.Infrastructure.ApiException;

namespace BackendFork.Services;

public sealed class LocalAiClient(HttpClient client, IOptions<AiOptions> options, PhotoService photos, IEquipmentRepository repository, TimeProvider clock)
{
    private static readonly SemaphoreSlim LiveGate = new(1, 1);

    // Camera preview is transient: it does not create photos, analyses or inspections.
    public async Task<AiResult> PreviewAsync(IFormFile file, CancellationToken ct)
    {
        Require(file.Length is > 0 and <= 512 * 1024, "Кадр должен быть не больше 512 КиБ.", 413);
        Require(await LiveGate.WaitAsync(0, ct), "Анализ занят. Дождитесь следующего кадра.", 503);
        try
        {
            await using var input = file.OpenReadStream();
            using var bounded = new MemoryStream();
            var buffer = new byte[8192];
            int read;
            while ((read = await input.ReadAsync(buffer, ct)) > 0)
            {
                Require(bounded.Length + read <= 512 * 1024, "Кадр слишком большой.", 413);
                bounded.Write(buffer, 0, read);
            }
            var bytes = bounded.ToArray();
            var jpeg = bytes.Length >= 3 && bytes[0] == 0xff && bytes[1] == 0xd8 && bytes[2] == 0xff;
            var png = bytes.Length >= 8 && bytes.AsSpan(0, 8).SequenceEqual(new byte[] {137,80,78,71,13,10,26,10});
            Require(jpeg || png, "Нужен JPEG или PNG кадр.", 415);
            bounded.Position = 0;
            return await InferAsync(bounded, jpeg ? "image/jpeg" : "image/png", Guid.Empty, Guid.Empty, Guid.Empty, TimeSpan.FromSeconds(15), ct);
        }
        finally { LiveGate.Release(); }
    }

    public async Task<Analysis> AnalyzeAsync(Guid equipmentId, Guid componentId, AnalyzeRequest request, string authorId, CancellationToken ct)
    {
        var photo = await repository.ReadAsync(s =>
        {
            var component = EquipmentService.FindComponent(EquipmentService.FindEquipment(s, equipmentId), componentId);
            EquipmentService.MatchInstallation(component, request.InstallationId);
            return s.Photos.Find(x => x.Id == request.PhotoId) ?? throw new ApiException(404, "Фото не найдено.");
        }, ct);
        Require(File.Exists(photos.PathFor(photo)), "Файл фото отсутствует.", 404);
        // Final inspection analysis waits ahead of incoming preview frames.
        Require(await LiveGate.WaitAsync(TimeSpan.FromSeconds(15), ct), "Анализ занят. Повторите сохранение через несколько секунд.", 503);
        AiResult result;
        try
        {
            var attempt = 0;
            while (true)
            {
                try
                {
                    await using var stream = File.OpenRead(photos.PathFor(photo));
                    result = await InferAsync(stream, photo.ContentType, equipmentId, componentId, request.InstallationId, client.Timeout, ct);
                    break;
                }
                catch (ApiException error) when (error.StatusCode == 503 && attempt++ < 4)
                { await Task.Delay(350, ct); }
            }
        }
        finally { LiveGate.Release(); }
        return await repository.WriteAsync(s =>
        {
            var component = EquipmentService.FindComponent(EquipmentService.FindEquipment(s, equipmentId), componentId);
            EquipmentService.MatchInstallation(component, request.InstallationId);
            var analysis = new Analysis
            {
                EquipmentId = equipmentId, ComponentId = componentId, InstallationId = request.InstallationId,
                PhotoId = request.PhotoId, AuthorId = authorId, CreatedAt = clock.GetUtcNow(),
                ModelVersion = result.ModelVersion, VisualStatus = result.VisualStatus!.Value,
                Summary = result.Summary, Recommendation = result.Recommendation,
                Confidence = result.Confidence, Detections = result.Detections
            };
            s.Analyses.Add(analysis);
            return analysis;
        }, ct);
    }

    private async Task<AiResult> InferAsync(Stream stream, string contentType, Guid equipmentId, Guid componentId,
        Guid installationId, TimeSpan timeout, CancellationToken ct)
    {
        Require(Uri.TryCreate(options.Value.BaseUrl, UriKind.Absolute, out var baseUri) &&
            baseUri.Scheme is "http" or "https", "Локальный ИИ-сервис ещё не настроен: укажите Ai:BaseUrl.", 503);
        var uri = new Uri(new Uri(baseUri!.AbsoluteUri.TrimEnd('/') + "/"), options.Value.AnalyzePath);
        AiResult result;
        try
        {
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
            deadline.CancelAfter(timeout);
            var aiToken = deadline.Token;
            using var form = new MultipartFormDataContent();
            using var fileContent = new StreamContent(stream);
            fileContent.Headers.ContentType = new MediaTypeHeaderValue(contentType);
            form.Add(fileContent, "file", "frame" + (contentType == "image/png" ? ".png" : ".jpg"));
            form.Add(new StringContent(equipmentId.ToString()), "equipmentId");
            form.Add(new StringContent(componentId.ToString()), "componentId");
            form.Add(new StringContent(installationId.ToString()), "installationId");
            using var message = new HttpRequestMessage(HttpMethod.Post, uri) { Content = form };
            using var response = await client.SendAsync(message, HttpCompletionOption.ResponseHeadersRead, aiToken);
            Require(response.StatusCode != System.Net.HttpStatusCode.ServiceUnavailable, "Анализ занят. Дождитесь следующего кадра.", 503);
            Require(response.IsSuccessStatusCode, "ИИ-сервис вернул HTTP " + (int)response.StatusCode + ".", 502);
            Require(response.Content.Headers.ContentLength is null or <= 1024 * 1024, "Слишком большой ответ ИИ.", 502);
            await using var body = await response.Content.ReadAsStreamAsync(aiToken);
            using var limited = new MemoryStream();
            var buffer = new byte[8192];
            int read;
            while ((read = await body.ReadAsync(buffer, aiToken)) > 0)
            {
                Require(limited.Length + read <= 1024 * 1024, "Слишком большой ответ ИИ.", 502);
                limited.Write(buffer, 0, read);
            }
            result = JsonSerializer.Deserialize<AiResult>(limited.ToArray(), JsonEquipmentRepository.JsonOptions)
                ?? throw new ApiException(502, "Пустой ответ ИИ.");
        }
        catch (HttpRequestException) { throw new ApiException(503, "Локальный ИИ-сервис недоступен."); }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested) { throw new ApiException(504, "ИИ-сервис не успел ответить."); }
        catch (JsonException) { throw new ApiException(502, "Ответ ИИ не соответствует JSON-контракту."); }
        ValidateResult(result);
        return result;
    }
    private static bool Probability(double? value) => value is null || (double.IsFinite(value.Value) && value is >= 0 and <= 1);
    private static bool Unit(double value) => double.IsFinite(value) && value is >= 0 and <= 1;
    private static void ValidateResult(AiResult result)
    {
        Require(result.VisualStatus is { } status && Enum.IsDefined(status), "ИИ должен вернуть visualStatus.", 502);
        Require(!string.IsNullOrWhiteSpace(result.Summary) && result.Summary.Length <= 4000, "Нужно краткое объяснение ИИ.", 502);
        Require(!string.IsNullOrWhiteSpace(result.Recommendation) && result.Recommendation.Length <= 4000, "Нужна рекомендация ИИ.", 502);
        Require(Probability(result.Confidence), "Уверенность ИИ должна быть от 0 до 1.", 502);
        Require(result.Detections is not null && result.Detections.Count <= 200, "Некорректный список рамок.", 502);
        foreach (var detection in result.Detections!)
        {
            Require(detection is not null && !string.IsNullOrWhiteSpace(detection.Label) && detection.Label.Length <= 200 &&
                Probability(detection.Confidence), "Некорректная метка ИИ.", 502);
            var b = detection!.Box;
            Require(b is not null && Unit(b.X) && Unit(b.Y) && Unit(b.Width) && Unit(b.Height) &&
                b.Width > 0 && b.Height > 0 && b.X + b.Width <= 1.000001 && b.Y + b.Height <= 1.000001,
                "Рамки должны быть в координатах исходного фото 0–1.", 502);
            if (detection.Polygon is { } polygon)
                Require(polygon.Count is >= 3 and <= 96 && polygon.All(point =>
                    point is { Length: 2 } && point.All(Unit)),
                    "Контур должен содержать от 3 до 96 точек исходного фото 0–1.", 502);
            if (detection.ModelIds is { } modelIds)
                Require(modelIds.Count is >= 1 and <= 16 && modelIds.All(id =>
                    !string.IsNullOrWhiteSpace(id) && id.Length <= 128), "Некорректные источники отметки ИИ.", 502);
        }
    }
}
