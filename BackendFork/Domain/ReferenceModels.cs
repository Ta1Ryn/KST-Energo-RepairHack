namespace BackendFork.Domain;

public enum DocumentCategory { Nameplate, Passport, Instruction, Report, Other }
public enum RecommendationTrigger { SensorOutOfRange, SensorAttention, ResourceAttention, ResourceExhausted, ServiceDue, VisualDefect }

public sealed class NameplateInfo
{
    public string? Manufacturer { get; set; }
    public string? Model { get; set; }
    public string? SerialNumber { get; set; }
    public int? ManufactureYear { get; set; }
    public string? Marking { get; set; }
    public Guid? DocumentId { get; set; }
}
public sealed class PassportInfo
{
    public string? Purpose { get; set; }
    public string? Location { get; set; }
    public string? OperatingMode { get; set; }
    public Dictionary<string,string> Parameters { get; set; } = [];
    public List<Guid> DocumentIds { get; set; } = [];
}
public sealed class StoredDocument
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid? EquipmentId { get; set; }
    public DocumentCategory Category { get; set; }
    public string FileName { get; set; } = "";
    public string OriginalName { get; set; } = "";
    public string ContentType { get; set; } = "";
    public string Sha256 { get; set; } = "";
    public long Size { get; set; }
    public string AuthorId { get; set; } = "";
    public DateTimeOffset UploadedAt { get; set; }
}
public sealed class SensorNorm
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string Name { get; set; } = "";
    public string SensorKind { get; set; } = "";
    public string Unit { get; set; } = "";
    public decimal? Min { get; set; }
    public decimal? Max { get; set; }
    public decimal? WarningMin { get; set; }
    public decimal? WarningMax { get; set; }
    public string? EquipmentModel { get; set; }
    public string? ComponentType { get; set; }
    public string? OperatingMode { get; set; }
    public Guid? SourceDocumentId { get; set; }
    public int Revision { get; set; } = 1;
    public bool IsDemo { get; set; }
}
public sealed class Instruction
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string Title { get; set; } = "";
    public string Text { get; set; } = "";
    public int Revision { get; set; } = 1;
    public Guid? DocumentId { get; set; }
    public bool IsDemo { get; set; }
}
public sealed class RecommendationRule
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public RecommendationTrigger Trigger { get; set; }
    public string? EquipmentModel { get; set; }
    public string? ComponentType { get; set; }
    public string? SensorKind { get; set; }
    public string? DefectCode { get; set; }
    public Guid InstructionId { get; set; }
    public string Message { get; set; } = "";
    public bool Enabled { get; set; } = true;
}
public sealed record Recommendation(string Reason, string Message, Guid? InstructionId, int? InstructionRevision, bool IsDemo);
public sealed record DefectType(string Code,string Name,string Description);
public static class DefectCatalog
{
    public static readonly DefectType[] All =
    [
        new("oilLeak","Течь масла","Утечка масла"),
        new("displacement","Смещение","Сдвиг узла или крепления"),
        new("corrosion","Коррозия","Коррозия поверхности"),
        new("geometryDeformation","Нарушение геометрии","Деформация детали")
    ];
    public static string? Code(string label) => label.ToLowerInvariant().Replace("_","").Replace(" ","") switch
    {
        "oilleak" => "oilLeak", "displacement" or "shift" => "displacement",
        "corrosion" or "rust" => "corrosion", "geometrydeformation" or "deformation" => "geometryDeformation", _ => null
    };
}
