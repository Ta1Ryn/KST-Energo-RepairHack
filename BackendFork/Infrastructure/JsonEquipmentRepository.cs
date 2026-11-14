using System.Text.Json;
using System.Text.Json.Serialization;
using BackendFork.Domain;
using Microsoft.Extensions.Options;

namespace BackendFork.Infrastructure;

// Replace this adapter once the supplied database schema is known.
public interface IEquipmentRepository
{
    Task InitializeAsync();
    Task<T> ReadAsync<T>(Func<DatabaseState, T> query, CancellationToken cancellationToken = default);
    Task<T> WriteAsync<T>(Func<DatabaseState, T> mutation, CancellationToken cancellationToken = default);
}
public sealed class JsonEquipmentRepository(IOptions<BackendOptions> options, IWebHostEnvironment environment) : IEquipmentRepository
{
    private readonly SemaphoreSlim gate = new(1, 1);
    private DatabaseState state = new();
    public static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true, Converters = { new JsonStringEnumConverter(JsonNamingPolicy.CamelCase) }
    };
    private string DirectoryPath => Path.GetFullPath(Path.Combine(environment.ContentRootPath, options.Value.DataDirectory));
    private string DatabasePath => Path.Combine(DirectoryPath, "database.json");
    public async Task InitializeAsync()
    {
        Directory.CreateDirectory(DirectoryPath);
        if (File.Exists(DatabasePath))
        {
            state = JsonSerializer.Deserialize<DatabaseState>(await File.ReadAllTextAsync(DatabasePath), JsonOptions)
                ?? throw new InvalidDataException("Локальное хранилище пусто или повреждено.");
            if (state.SchemaVersion != 1) throw new InvalidDataException("Неизвестная версия локального хранилища.");
        }
        else
        {
            state = options.Value.SeedDemoData ? DemoData.Create() : new();
            await SaveAsync(state, CancellationToken.None);
        }
        ReferenceData.Upgrade(state, options.Value.SeedDemoData);
        await SaveAsync(state, CancellationToken.None);
    }
    private static DatabaseState Copy(DatabaseState source) =>
        JsonSerializer.Deserialize<DatabaseState>(JsonSerializer.SerializeToUtf8Bytes(source, JsonOptions), JsonOptions)!;
    public async Task<T> ReadAsync<T>(Func<DatabaseState, T> query, CancellationToken cancellationToken = default)
    {
        await gate.WaitAsync(cancellationToken);
        try { return query(Copy(state)); }
        finally { gate.Release(); }
    }
    public async Task<T> WriteAsync<T>(Func<DatabaseState, T> mutation, CancellationToken cancellationToken = default)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            var next = Copy(state);
            var result = mutation(next);
            await SaveAsync(next, cancellationToken);
            state = next;
            return result;
        }
        finally { gate.Release(); }
    }
    private async Task SaveAsync(DatabaseState next, CancellationToken cancellationToken)
    {
        var temporary = DatabasePath + ".tmp";
        try
        {
            await File.WriteAllTextAsync(temporary, JsonSerializer.Serialize(next, JsonOptions), cancellationToken);
            File.Move(temporary, DatabasePath, overwrite: true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
