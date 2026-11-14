namespace BackendFork.Infrastructure;

public sealed class DatabaseOptions
{
    public string Provider { get; set; } = "Json";
    public string? ConnectionString { get; set; }
    public bool ImportExistingJson { get; set; } = true;
}
public sealed class ReportOptions
{
    public string FontPath { get; set; } = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Fonts),"arial.ttf");
}
