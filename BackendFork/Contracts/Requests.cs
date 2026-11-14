using System.ComponentModel.DataAnnotations;
using BackendFork.Domain;

namespace BackendFork.Contracts;

public sealed class SetupRequest
{
    [Required, StringLength(100, MinimumLength = 1)] public string DisplayName { get; set; } = "";
    [Required, RegularExpression("^[0-9]{4,12}$")] public string Pin { get; set; } = "";
}
public sealed class LoginRequest
{
    [Required, RegularExpression("^[0-9]{4,12}$")] public string Pin { get; set; } = "";
}
public sealed record SessionResponse(string Token, DateTimeOffset ExpiresAt, string EngineerId, string DisplayName);
public sealed class EquipmentRequest
{
    [Required, StringLength(200)] public string Name { get; set; } = "";
    [Required, StringLength(200)] public string Model { get; set; } = "";
    [Required, StringLength(200)] public string SerialNumber { get; set; } = "";
    [Required] public Dictionary<string, string> Characteristics { get; set; } = [];
    [Required, MinLength(1), MaxLength(200)] public List<ComponentRequest> Components { get; set; } = [];
}
public class ComponentDefinitionRequest
{
    [Required, StringLength(200)] public string Name { get; set; } = "";
    [Required, StringLength(100)] public string Type { get; set; } = "";
    public decimal? NormLifetimeHours { get; set; }
    public decimal? ServiceIntervalHours { get; set; }
    [Required, MaxLength(100)] public List<SensorDefinitionRequest> Sensors { get; set; } = [];
}
public sealed class ComponentRequest : ComponentDefinitionRequest
{
    public DateTimeOffset InstalledAt { get; set; }
    [StringLength(200)] public string? SerialNumber { get; set; }
    public decimal? OperatingHours { get; set; }
    public DateTimeOffset? HoursMeasuredAt { get; set; }
    public DateTimeOffset? LastServiceAt { get; set; }
    public decimal? HoursAtLastService { get; set; }
}
public sealed class SensorDefinitionRequest
{
    [StringLength(100)] public string? Kind { get; set; }
    public Guid? NormId { get; set; }
    [Required, StringLength(100)] public string Key { get; set; } = "";
    [Required, StringLength(200)] public string Name { get; set; } = "";
    [StringLength(50)] public string Unit { get; set; } = "";
    public decimal? Min { get; set; }
    public decimal? Max { get; set; }
}
public sealed class TelemetryRequest
{
    public Guid InstallationId { get; set; }
    public DateTimeOffset MeasuredAt { get; set; }
    public decimal? OperatingHours { get; set; }
    [Required, MaxLength(100)] public List<SensorReadingRequest> Sensors { get; set; } = [];
}
public sealed class SensorReadingRequest
{
    [StringLength(50)] public string? Unit { get; set; }
    [Required, StringLength(100)] public string Key { get; set; } = "";
    public decimal Value { get; set; }
}
public sealed class AnalyzeRequest
{
    public Guid InstallationId { get; set; }
    public Guid PhotoId { get; set; }
}
// The independent YOLO service implements this response contract.
public sealed class AiResult
{
    public VisualStatus? VisualStatus { get; set; }
    public string Summary { get; set; } = "";
    public string Recommendation { get; set; } = "";
    public string? ModelVersion { get; set; }
    public double? Confidence { get; set; }
    public List<Detection> Detections { get; set; } = [];
}
public sealed class InspectionRequest
{
    public DateTimeOffset PerformedAt { get; set; }
    [StringLength(4000)] public string? Comment { get; set; }
    [Required, MinLength(1), MaxLength(200)] public List<InspectionItemRequest> Items { get; set; } = [];
}
public sealed class InspectionItemRequest
{
    [StringLength(60)] public string? DefectTypeCode { get; set; }
    public Guid ComponentId { get; set; }
    public Guid InstallationId { get; set; }
    public WorkAction Action { get; set; }
    public bool DefectResolved { get; set; }
    [StringLength(4000)] public string? Comment { get; set; }
    public Guid? AnalysisId { get; set; }
    [Required, MaxLength(20)] public List<Guid> PhotoIds { get; set; } = [];
    public ReplacementRequest? Replacement { get; set; }
}
public sealed class ReplacementRequest
{
    [StringLength(200)] public string? SerialNumber { get; set; }
    public decimal InitialOperatingHours { get; set; }
}
