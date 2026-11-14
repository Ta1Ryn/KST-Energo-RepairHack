using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Infrastructure;
using Microsoft.Extensions.Options;
using static BackendFork.Infrastructure.ApiException;

namespace BackendFork.Services;

public sealed class PhotoService(IEquipmentRepository repository, IOptions<BackendOptions> options, IWebHostEnvironment environment, TimeProvider clock)
{
    private string DirectoryPath => Path.GetFullPath(Path.Combine(environment.ContentRootPath, options.Value.DataDirectory, "photos"));
    public string PathFor(Photo photo) => Path.Combine(DirectoryPath, photo.FileName);
    public Task<Photo> GetAsync(Guid id, CancellationToken ct) =>
        repository.ReadAsync(s => s.Photos.Find(x => x.Id == id) ?? throw new ApiException(404, "Фото не найдено."), ct);
    public static PhotoResponse Response(Photo photo) =>
        new(photo.Id, photo.OriginalName, photo.ContentType, photo.Size, photo.UploadedAt, "/api/photos/" + photo.Id);

    public async Task<PhotoResponse> UploadAsync(IFormFile file, string authorId, CancellationToken ct)
    {
        var max = Math.Clamp(options.Value.MaxPhotoBytes, 1024, 10 * 1024 * 1024);
        Require(file.Length > 0, "Файл пуст.");
        Require(file.Length <= max, "Максимальный размер фото: " + max + " байт.", 413);
        await using var input = file.OpenReadStream();
        var header = new byte[12];
        var headerSize = await input.ReadAtLeastAsync(header, 12, throwOnEndOfStream: false, cancellationToken: ct);
        var format = Detect(header.AsSpan(0, headerSize));
        Require(format is not null, "Поддерживаются JPEG, PNG и WebP.", 415);
        var photo = new Photo
        {
            OriginalName = Path.GetFileName(file.FileName).Replace("\\", "_"),
            FileName = Guid.NewGuid().ToString("N") + format!.Value.Extension, ContentType = format.Value.ContentType,
            Size = file.Length, UploadedAt = clock.GetUtcNow(), AuthorId = authorId
        };
        Directory.CreateDirectory(DirectoryPath);
        var target = PathFor(photo);
        try
        {
            await using (var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, true))
            {
                await output.WriteAsync(header.AsMemory(0, headerSize), ct);
                var buffer = new byte[81920];
                long length = headerSize;
                int read;
                while ((read = await input.ReadAsync(buffer, ct)) > 0)
                {
                    length += read;
                    Require(length <= max, "Фото слишком большое.", 413);
                    await output.WriteAsync(buffer.AsMemory(0, read), ct);
                }
                photo.Size = length;
            }
            await repository.WriteAsync(s => { s.Photos.Add(photo); return true; }, ct);
            return Response(photo);
        }
        catch { if (File.Exists(target)) File.Delete(target); throw; }
    }
    private static (string Extension, string ContentType)? Detect(ReadOnlySpan<byte> bytes)
    {
        if (bytes.Length >= 3 && bytes[0] == 0xff && bytes[1] == 0xd8 && bytes[2] == 0xff) return (".jpg", "image/jpeg");
        if (bytes.Length >= 8 && bytes[..8].SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 })) return (".png", "image/png");
        if (bytes.Length >= 12 && bytes[..4].SequenceEqual("RIFF"u8) && bytes[8..12].SequenceEqual("WEBP"u8)) return (".webp", "image/webp");
        return null;
    }
}
