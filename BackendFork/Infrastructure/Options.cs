namespace BackendFork.Infrastructure;

public sealed class BackendOptions
{
    public string DataDirectory { get; set; } = "App_Data";
    public bool SeedDemoData { get; set; }
    public int SessionHours { get; set; } = 12;
    public decimal WarningRemainingFraction { get; set; } = 0.1m;
    public int TelemetryMaxAgeMinutes { get; set; } = 60;
    public long MaxPhotoBytes { get; set; } = 10 * 1024 * 1024;
}
public sealed class AiOptions
{
    public string? BaseUrl { get; set; }
    public string AnalyzePath { get; set; } = "analyze";
    public int TimeoutSeconds { get; set; } = 30;
}
