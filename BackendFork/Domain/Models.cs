namespace BackendFork.Domain;

public enum VisualStatus { NoVisibleProblems, CheckRecommended, PossibleDefect }
public enum ResourceStatus { Unknown, Normal, Attention, Exhausted }
public enum SensorStatus { Unknown, Normal, OutOfRange, Stale, Attention }
public enum WorkAction { Checked, DefectRecorded, Serviced, Replaced }

public sealed class DatabaseState
{
    public int SchemaVersion { get; set; } = 1;
    public PinAccount? Account { get; set; }
    public List<Equipment> Equipment { get; set; } = [];
    public List<Photo> Photos { get; set; } = [];
    public List<Analysis> Analyses { get; set; } = [];
    public List<Inspection> Inspections { get; set; } = [];
    public List<SensorNorm> SensorNorms { get; set; } = [];
    public List<Instruction> Instructions { get; set; } = [];
    public List<RecommendationRule> RecommendationRules { get; set; } = [];
    public List<StoredDocument> Documents { get; set; } = [];
}
public sealed class PinAccount
{
    public string Id { get; set; } = "engineer";
    public string DisplayName { get; set; } = "";
    public string Salt { get; set; } = "";
    public string Hash { get; set; } = "";
}
public sealed class Equipment
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string Name { get; set; } = "";
    public string Model { get; set; } = "";
    public string SerialNumber { get; set; } = "";
    public string QrCode { get; set; } = "";
    public Dictionary<string, string> Characteristics { get; set; } = [];
    public List<Component> Components { get; set; } = [];
    public NameplateInfo Nameplate { get; set; } = new();
    public PassportInfo Passport { get; set; } = new();
}
// Id identifies the component slot; Current.Id changes after replacement.
public sealed class Component
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string Name { get; set; } = "";
    public string Type { get; set; } = "";
    public decimal? NormLifetimeHours { get; set; }
    public decimal? ServiceIntervalHours { get; set; }
    public Installation Current { get; set; } = new();
    public List<Installation> PreviousInstallations { get; set; } = [];
    public List<Sensor> Sensors { get; set; } = [];
}
public sealed class Installation
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string? SerialNumber { get; set; }
    public DateTimeOffset InstalledAt { get; set; }
    public DateTimeOffset? RemovedAt { get; set; }
    public decimal? OperatingHours { get; set; }
    public DateTimeOffset? HoursMeasuredAt { get; set; }
    public DateTimeOffset? LastServiceAt { get; set; }
    public decimal? HoursAtLastService { get; set; }
}
public sealed class Sensor
{
    public string Kind { get; set; } = "";
    public Guid? NormId { get; set; }
    public string Key { get; set; } = "";
    public string Name { get; set; } = "";
    public string Unit { get; set; } = "";
    public decimal? Min { get; set; }
    public decimal? Max { get; set; }
    public decimal? Value { get; set; }
    public DateTimeOffset? MeasuredAt { get; set; }
}
public sealed class Photo
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string FileName { get; set; } = "";
    public string OriginalName { get; set; } = "";
    public string ContentType { get; set; } = "";
    public long Size { get; set; }
    public DateTimeOffset UploadedAt { get; set; }
    public string AuthorId { get; set; } = "";
}
public sealed class NormalizedBox
{
    public double X { get; set; }
    public double Y { get; set; }
    public double Width { get; set; }
    public double Height { get; set; }
}
public sealed class Detection
{
    public string Label { get; set; } = "";
    public double? Confidence { get; set; }
    public NormalizedBox Box { get; set; } = new();
    [System.Text.Json.Serialization.JsonIgnore(Condition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull)]
    public List<double[]>? Polygon { get; set; }
    [System.Text.Json.Serialization.JsonIgnore(Condition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull)]
    public List<string>? ModelIds { get; set; }
}
public sealed class Analysis
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid EquipmentId { get; set; }
    public Guid ComponentId { get; set; }
    public Guid InstallationId { get; set; }
    public Guid PhotoId { get; set; }
    public DateTimeOffset CreatedAt { get; set; }
    public string AuthorId { get; set; } = "";
    public string? ModelVersion { get; set; }
    public VisualStatus VisualStatus { get; set; }
    public string Summary { get; set; } = "";
    public string Recommendation { get; set; } = "";
    public double? Confidence { get; set; }
    public List<Detection> Detections { get; set; } = [];
}
public sealed class Inspection
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid EquipmentId { get; set; }
    public DateTimeOffset PerformedAt { get; set; }
    public DateTimeOffset RecordedAt { get; set; }
    public string AuthorId { get; set; } = "";
    public string AuthorName { get; set; } = "";
    public string? Comment { get; set; }
    public List<InspectionItem> Items { get; set; } = [];
    public BackendFork.Contracts.EquipmentCard? EquipmentSnapshot { get; set; }
}
public sealed class InspectionItem
{
    public string? DefectTypeCode { get; set; }
    public Guid? FindingId { get; set; }
    public Guid ComponentId { get; set; }
    public Guid InstallationId { get; set; }
    public Guid? NewInstallationId { get; set; }
    public WorkAction Action { get; set; }
    public bool DefectResolved { get; set; }
    public decimal? OperatingHoursAtWork { get; set; }
    public string? Comment { get; set; }
    public Guid? AnalysisId { get; set; }
    public List<Guid> PhotoIds { get; set; } = [];
}
