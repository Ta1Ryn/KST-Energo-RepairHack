using System.ComponentModel.DataAnnotations;
using BackendFork.Domain;

namespace BackendFork.Contracts;

public sealed class SensorNormRequest
{
    [Required,StringLength(200)] public string Name { get; set; } = "";
    [Required,StringLength(100)] public string SensorKind { get; set; } = "";
    [Required,StringLength(50)] public string Unit { get; set; } = "";
    public decimal? Min { get; set; }
    public decimal? Max { get; set; }
    public decimal? WarningMin { get; set; }
    public decimal? WarningMax { get; set; }
    [StringLength(200)] public string? EquipmentModel { get; set; }
    [StringLength(100)] public string? ComponentType { get; set; }
    [StringLength(100)] public string? OperatingMode { get; set; }
    public Guid? SourceDocumentId { get; set; }
    public bool IsDemo { get; set; }
}
public sealed class InstructionRequest
{
    [Required,StringLength(200)] public string Title { get; set; } = "";
    [StringLength(20000)] public string? Text { get; set; }
    public Guid? DocumentId { get; set; }
    public bool IsDemo { get; set; }
}
public sealed class RecommendationRuleRequest
{
    public RecommendationTrigger Trigger { get; set; }
    [StringLength(200)] public string? EquipmentModel { get; set; }
    [StringLength(100)] public string? ComponentType { get; set; }
    [StringLength(100)] public string? SensorKind { get; set; }
    [StringLength(100)] public string? DefectCode { get; set; }
    public Guid InstructionId { get; set; }
    [Required,StringLength(2000)] public string Message { get; set; } = "";
    public bool Enabled { get; set; } = true;
}
public sealed class PassportRequest
{
    [Required] public NameplateInfo Nameplate { get; set; } = new();
    [Required] public PassportInfo Passport { get; set; } = new();
}
public sealed record DocumentResponse(Guid Id,Guid? EquipmentId,DocumentCategory Category,string OriginalName,
    string ContentType,long Size,string Sha256,DateTimeOffset UploadedAt,string Url);
