using Microsoft.AspNetCore.Diagnostics;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Infrastructure;

public sealed class ApiException(int statusCode, string message) : Exception(message)
{
    public int StatusCode { get; } = statusCode;
    public static void Require([System.Diagnostics.CodeAnalysis.DoesNotReturnIf(false)] bool condition, string message, int status = 400)
    {
        if (!condition) throw new ApiException(status, message);
    }
}
public sealed class ApiExceptionHandler(ILogger<ApiExceptionHandler> logger) : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(HttpContext context, Exception exception, CancellationToken cancellationToken)
    {
        var sqlState = exception is System.Data.Common.DbException db ? db.SqlState : null;
        var status = exception is ApiException api ? api.StatusCode : sqlState switch { "23505" => 409, "23503" or "23514" or "22P02" => 400, _ => 500 };
        if (status == 500) logger.LogError(exception, "Ошибка обработки запроса {TraceId}", context.TraceIdentifier);
        context.Response.StatusCode = status;
        await context.Response.WriteAsJsonAsync(new ProblemDetails
        {
            Status = status,
            Title = status switch
            {
                400 => "Некорректный запрос", 401 => "Требуется вход", 403 => "Доступ запрещён",
                404 => "Не найдено", 409 => "Конфликт данных", 413 => "Слишком большой файл",
                415 => "Неподдерживаемый формат", 502 => "Ошибка ИИ-сервиса", 503 => "Сервис недоступен",
                504 => "Истекло время ожидания ИИ-сервиса", _ => "Ошибка сервера"
            },
            Detail = status == 500 ? "Не удалось выполнить запрос." : sqlState is not null ? (status == 409 ? "Запись с таким кодом уже существует." : "Проверьте значения и связанные записи.") : exception.Message,
            Extensions = { ["traceId"] = context.TraceIdentifier }
        }, cancellationToken);
        return true;
    }
}
