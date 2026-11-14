using BackendFork.Domain;

namespace BackendFork.Contracts;

public sealed record Page<T>(IReadOnlyList<T> Items, int Total, int Offset, int Limit);
public sealed record EquipmentSummary(Guid Id, string Name, string Model, string SerialNumber, int ComponentCount);
public sealed record EquipmentCard(Guid Id, string Name, string Model, string SerialNumber,
    Dictionary<string, string> Characteristics, IReadOnlyList<ComponentCard> Components, NameplateInfo? Nameplate = null, PassportInfo? Passport = null);
public sealed record ResourceAssessment(
    decimal? NormLifetimeHours, decimal? OperatingHours, decimal? RemainingHours, decimal? RemainingFraction,
    ResourceStatus LifetimeStatus, decimal? ServiceIntervalHours, decimal? HoursSinceService,
    decimal? HoursUntilService, ResourceStatus ServiceStatus, bool DataStale, string[] Limitations);
public sealed record SensorAssessment(string Key, string Name, string Unit, decimal? Min, decimal? Max,
    decimal? Value, DateTimeOffset? MeasuredAt, SensorStatus Status, Guid? NormId = null, int? NormRevision = null,
    decimal? WarningMin = null, decimal? WarningMax = null);
public sealed record ComponentCard(Guid Id, string Name, string Type, Installation Current,
    IReadOnlyList<Installation> PreviousInstallations, ResourceAssessment Resource,
    IReadOnlyList<SensorAssessment> Sensors, Analysis? LatestVisualAnalysis, bool RequiresAttention,
    IReadOnlyList<string> Warnings, IReadOnlyList<Recommendation>? Recommendations = null);
public sealed record PhotoResponse(Guid Id, string OriginalName, string ContentType, long Size, DateTimeOffset UploadedAt, string Url);
