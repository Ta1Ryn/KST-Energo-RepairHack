using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Infrastructure;
using Microsoft.Extensions.Options;
using static BackendFork.Infrastructure.ApiException;

namespace BackendFork.Services;

public sealed class EquipmentService(IEquipmentRepository repository, IOptions<BackendOptions> options, TimeProvider clock)
{
    public static Equipment FindEquipment(DatabaseState state, Guid id) =>
        state.Equipment.Find(x => x.Id == id) ?? throw new ApiException(404, "Оборудование не найдено.");
    public static Component FindComponent(Equipment equipment, Guid id) =>
        equipment.Components.Find(x => x.Id == id) ?? throw new ApiException(404, "Компонент не найден.");
    public static void MatchInstallation(Component component, Guid installationId) =>
        Require(component.Current.Id == installationId, "Компонент заменён. Обновите карточку и повторите запрос.", 409);
    public static void PageBounds(int offset, int limit) =>
        Require(offset >= 0 && limit is >= 1 and <= 100, "offset >= 0, limit от 1 до 100.");

    public Task<Page<EquipmentSummary>> ListAsync(string? search, int offset, int limit, CancellationToken ct)
    {
        PageBounds(offset, limit);
        return repository.ReadAsync(s =>
        {
            var query = s.Equipment.Where(e => string.IsNullOrWhiteSpace(search) ||
                new[] { e.Name, e.Model, e.SerialNumber }.Any(v => v.Contains(search, StringComparison.OrdinalIgnoreCase)))
                .OrderBy(e => e.Name).ToList();
            return new Page<EquipmentSummary>(query.Skip(offset).Take(limit).Select(e =>
                new EquipmentSummary(e.Id, e.Name, e.Model, e.SerialNumber, e.Components.Count)).ToList(),
                query.Count, offset, limit);
        }, ct);
    }
    public Task<EquipmentCard> GetAsync(Guid id, CancellationToken ct) =>
        repository.ReadAsync(s => Card(s, FindEquipment(s, id)), ct);
    public Task<ComponentCard> GetComponentAsync(Guid equipmentId, Guid componentId, CancellationToken ct) =>
        repository.ReadAsync(s => Card(s, FindComponent(FindEquipment(s, equipmentId), componentId)), ct);
    public Task<EquipmentCard> CreateAsync(EquipmentRequest request, CancellationToken ct) => repository.WriteAsync(s =>
    {
        Require(!s.Equipment.Any(e => e.Model.Equals(request.Model.Trim(), StringComparison.OrdinalIgnoreCase) &&
            e.SerialNumber.Equals(request.SerialNumber.Trim(), StringComparison.OrdinalIgnoreCase)),
            "Оборудование с этой моделью и заводским номером уже существует.", 409);
        var equipment = new Equipment
        {
            Name = request.Name.Trim(), Model = request.Model.Trim(), SerialNumber = request.SerialNumber.Trim(),
            Characteristics = request.Characteristics,
            Nameplate = new NameplateInfo { Model=request.Model.Trim(), SerialNumber=request.SerialNumber.Trim() }
        };
        // Retain the legacy database column without exposing it in the API or scanning workflow.
        equipment.QrCode = equipment.Id.ToString();
        foreach (var input in request.Components)
        {
            equipment.Components.Add(NewComponent(s, equipment, input));
        }
        s.Equipment.Add(equipment);
        return Card(s, equipment);
    }, ct);

    public Task<ComponentCard> AddComponentAsync(Guid equipmentId, ComponentRequest request, CancellationToken ct) => repository.WriteAsync(s =>
    {
        var equipment = FindEquipment(s, equipmentId);
        Require(equipment.Components.Count < 200, "Максимум 200 компонентов оборудования.");
        var component = NewComponent(s, equipment, request);
        equipment.Components.Add(component);
        return Card(s, component);
    }, ct);

    private Component NewComponent(DatabaseState state, Equipment equipment, ComponentRequest input)
    {
        Require(input is not null, "Компонент не может быть null.");
        ValidateDefinition(input);
        ValidateTime(input.InstalledAt, "InstalledAt");
        Require(input.OperatingHours is null or >= 0, "Наработка не может быть отрицательной.");
        Require(input.HoursMeasuredAt.HasValue == input.OperatingHours.HasValue, "Наработка и время её измерения задаются вместе.");
        if (input.HoursMeasuredAt is { } measured)
        {
            ValidateTime(measured, "HoursMeasuredAt");
            Require(measured >= input.InstalledAt, "Измерение не может быть раньше установки.");
        }
        if (input.LastServiceAt is { } service)
        {
            ValidateTime(service, "LastServiceAt");
            Require(service >= input.InstalledAt, "Обслуживание не может быть раньше установки.");
            Require(input.HoursMeasuredAt is null || service <= input.HoursMeasuredAt,
                "Обслуживание не может быть позже последнего измерения наработки.");
        }
        Require(input.HoursAtLastService is null or >= 0, "Наработка при обслуживании не может быть отрицательной.");
        Require(input.HoursAtLastService is null || (input.LastServiceAt is not null &&
            input.OperatingHours is not null && input.HoursAtLastService <= input.OperatingHours),
            "Наработка при обслуживании должна быть не больше текущей.");
        var component = new Component
        {
            Current = new Installation
            {
                InstalledAt = input.InstalledAt, SerialNumber = input.SerialNumber,
                OperatingHours = input.OperatingHours, HoursMeasuredAt = input.HoursMeasuredAt,
                LastServiceAt = input.LastServiceAt, HoursAtLastService = input.HoursAtLastService
            }
        };
        ApplyDefinition(state, component, input, equipment);
        return component;
    }

    public Task<ComponentCard> UpdateDefinitionAsync(Guid equipmentId, Guid componentId, ComponentDefinitionRequest request, CancellationToken ct) =>
        repository.WriteAsync(s =>
        {
            ValidateDefinition(request);
            var component = FindComponent(FindEquipment(s, equipmentId), componentId);
            ApplyDefinition(s, component, request, FindEquipment(s, equipmentId));
            return Card(s, component);
        }, ct);
    private static void ValidateDefinition(ComponentDefinitionRequest request)
    {
        Require(request.NormLifetimeHours is null or > 0, "Нормативный ресурс должен быть больше нуля.");
        Require(request.ServiceIntervalHours is null or > 0, "Интервал обслуживания должен быть больше нуля.");
        Require(request.Sensors.All(x => x is not null), "Датчик не может быть null.");
        Require(request.Sensors.Select(x => x.Key.Trim()).Distinct(StringComparer.Ordinal).Count() == request.Sensors.Count,
            "Ключи датчиков должны быть уникальны в компоненте.");
        foreach (var sensor in request.Sensors)
        {
            Require(sensor is not null, "Датчик не может быть null.");
            Require(sensor.Min is null || sensor.Max is null || sensor.Min <= sensor.Max, "Min датчика не может превышать Max.");
        }
    }
    private static void ApplyDefinition(DatabaseState state, Component component, ComponentDefinitionRequest request, Equipment equipment)
    {
        component.Name = request.Name.Trim();
        component.Type = request.Type.Trim();
        component.NormLifetimeHours = request.NormLifetimeHours;
        component.ServiceIntervalHours = request.ServiceIntervalHours;
        component.Sensors = request.Sensors.Select(input =>
        {
            var old = component.Sensors.Find(x => x.Key == input.Key.Trim());
            var kind=input.Kind??input.Key.Trim();
            SensorNorm? norm=null;
            if(input.NormId is{} normId)
            {
                norm=state.SensorNorms.Find(x=>x.Id==normId)??throw new ApiException(404,"Норма не найдена.");
                var candidate=new Sensor{Kind=kind,Unit=input.Unit};
                Require(ReferenceService.NormMatches(norm,equipment,component,candidate),"Норма не подходит к модели, режиму или единицам датчика.");
                Require(input.Min is null && input.Max is null,"При normId границы задаются в справочнике норм.");
            }
            else if(input.Min is not null || input.Max is not null)
            {
                norm=new SensorNorm{Name=component.Name+": "+input.Name,SensorKind=kind,Unit=input.Unit,Min=input.Min,Max=input.Max,
                    EquipmentModel=equipment.Model,ComponentType=component.Type,IsDemo=equipment.Model.StartsWith("DEMO")};
                state.SensorNorms.Add(norm);
            }
            Require(old?.Value is null || old.Unit==input.Unit,"Для изменения единиц датчика с историей создайте новый sensor key.",409);
            return new Sensor
            {
                Kind=kind,NormId=norm?.Id,
                Key = input.Key.Trim(), Name = input.Name.Trim(), Unit = input.Unit, Min = norm?.Min, Max = norm?.Max,
                Value = old?.Value, MeasuredAt = old?.MeasuredAt
            };
        }).ToList();
    }

    public Task<ComponentCard> TelemetryAsync(Guid equipmentId, Guid componentId, TelemetryRequest request, CancellationToken ct) =>
        repository.WriteAsync(s =>
        {
            var component = FindComponent(FindEquipment(s, equipmentId), componentId);
            MatchInstallation(component, request.InstallationId);
            ValidateTime(request.MeasuredAt, "MeasuredAt");
            Require(request.MeasuredAt >= component.Current.InstalledAt, "Телеметрия не может быть раньше установки.");
            Require(request.OperatingHours is not null || request.Sensors.Count > 0, "Нужна наработка или показания датчиков.");
            Require(request.Sensors.All(x => x is not null), "Показание датчика не может быть null.");
            Require(request.Sensors.Select(x => x.Key).Distinct().Count() == request.Sensors.Count, "Повторяющиеся ключи датчиков.");
            if (request.OperatingHours is { } hours)
            {
                Require(hours >= 0 && (component.Current.OperatingHours is null || hours >= component.Current.OperatingHours),
                    "Наработка не может уменьшаться. Для нового компонента зарегистрируйте замену.", 409);
                Require(component.Current.HoursMeasuredAt is null || request.MeasuredAt >= component.Current.HoursMeasuredAt,
                    "Измерение наработки устарело.", 409);
                Require(component.Current.LastServiceAt is null || request.MeasuredAt >= component.Current.LastServiceAt,
                    "Измерение раньше последнего обслуживания.", 409);
                Require(component.Current.HoursMeasuredAt != request.MeasuredAt || component.Current.OperatingHours == hours,
                    "Другое значение наработки с той же датой измерения.", 409);
                component.Current.OperatingHours = hours;
                component.Current.HoursMeasuredAt = request.MeasuredAt;
            }
            foreach (var input in request.Sensors)
            {
                var sensor = component.Sensors.Find(x => x.Key == input.Key)
                    ?? throw new ApiException(400, "Неизвестный датчик: " + input.Key);
                Require(input.Unit is null || input.Unit==sensor.Unit,"Единицы показания не совпадают с датчиком.");
                Require(sensor.MeasuredAt is null || request.MeasuredAt >= sensor.MeasuredAt, "Показание датчика устарело.", 409);
                Require(sensor.MeasuredAt != request.MeasuredAt || sensor.Value == input.Value, "Другое показание с той же датой.", 409);
                sensor.Value = input.Value;
                sensor.MeasuredAt = request.MeasuredAt;
            }
            return Card(s, component);
        }, ct);

    public Task<Inspection> InspectAsync(Guid equipmentId, InspectionRequest request, string authorId, string authorName, CancellationToken ct) =>
        repository.WriteAsync(s =>
        {
            var equipment = FindEquipment(s, equipmentId);
            ValidateTime(request.PerformedAt, "PerformedAt");
            Require(request.Items.All(x => x is not null), "Элемент осмотра не может быть null.");
            Require(request.Items.Select(i => i.ComponentId).Distinct().Count() == request.Items.Count, "Компонент повторяется в осмотре.");
            var inspection = new Inspection
            {
                EquipmentId = equipmentId, PerformedAt = request.PerformedAt, RecordedAt = clock.GetUtcNow(),
                AuthorId = authorId, AuthorName = authorName, Comment = request.Comment,
                EquipmentSnapshot = System.Text.Json.JsonSerializer.Deserialize<EquipmentCard>(
                    System.Text.Json.JsonSerializer.SerializeToUtf8Bytes(Card(s,equipment),JsonEquipmentRepository.JsonOptions),
                    JsonEquipmentRepository.JsonOptions)
            };
            foreach (var input in request.Items)
            {
                Require(Enum.IsDefined(input.Action), "Неизвестный вид работы.");
                Require(input.DefectTypeCode is null || (input.Action == WorkAction.DefectRecorded && DefectCatalog.All.Any(x=>x.Code==input.DefectTypeCode)), "Неизвестный тип дефекта или неверный вид работы.");
                var component = FindComponent(equipment, input.ComponentId);
                MatchInstallation(component, input.InstallationId);
                Require(request.PerformedAt >= component.Current.InstalledAt, "Осмотр раньше установки компонента.");
                var previousWork = s.Inspections.Where(x => x.EquipmentId == equipmentId &&
                    x.Items.Any(i => i.ComponentId == input.ComponentId)).Select(x => (DateTimeOffset?)x.PerformedAt).Max();
                Require(previousWork is null || request.PerformedAt >= previousWork, "Работа раньше последней зарегистрированной работы.", 409);
                Require(input.PhotoIds.All(id => s.Photos.Any(p => p.Id == id)), "Фото не найдено.", 404);
                if (input.AnalysisId is { } analysisId)
                    Require(s.Analyses.Any(a => a.Id == analysisId && a.ComponentId == component.Id &&
                        a.EquipmentId == equipmentId && a.InstallationId == input.InstallationId),
                        "Анализ не принадлежит этой установке компонента.", 400);
                Require(!input.DefectResolved || input.Action is WorkAction.Checked or WorkAction.Serviced,
                    "Подтверждение устранения дефекта допустимо при проверке или обслуживании.");
                Require(input.Action != WorkAction.DefectRecorded || !string.IsNullOrWhiteSpace(input.Comment),
                    "При регистрации дефекта нужно замечание.");
                Require((input.Action == WorkAction.Replaced) == (input.Replacement is not null),
                    "Replacement требуется только для замены.");
                var item = new InspectionItem
                {
                    ComponentId = component.Id, InstallationId = component.Current.Id, Action = input.Action,
                    Comment = input.Comment, AnalysisId = input.AnalysisId, PhotoIds = input.PhotoIds,
                    DefectResolved = input.DefectResolved,
                    DefectTypeCode = input.DefectTypeCode,
                    FindingId = input.DefectTypeCode is null ? null : Guid.NewGuid(),
                    OperatingHoursAtWork = component.Current.OperatingHours
                };
                if (input.Action is WorkAction.Serviced or WorkAction.Replaced)
                {
                    Require(component.Current.LastServiceAt is null || request.PerformedAt >= component.Current.LastServiceAt,
                        "Работа раньше последнего обслуживания.", 409);
                    Require(component.Current.HoursMeasuredAt is null || request.PerformedAt >= component.Current.HoursMeasuredAt,
                        "Работа раньше текущего показания счётчика. Нужен осмотр с актуальной датой.", 409);
                }
                if (input.Action == WorkAction.Serviced)
                {
                    component.Current.LastServiceAt = request.PerformedAt;
                    // A stale or absent counter cannot establish an accurate service baseline.
                    component.Current.HoursAtLastService = IsStale(component.Current.HoursMeasuredAt)
                        ? null : component.Current.OperatingHours;
                }
                if (input.Action == WorkAction.Replaced)
                {
                    Require(input.Replacement!.InitialOperatingHours >= 0, "Начальная наработка не может быть отрицательной.");
                    component.Current.RemovedAt = request.PerformedAt;
                    component.PreviousInstallations.Add(component.Current);
                    component.Current = new Installation
                    {
                        InstalledAt = request.PerformedAt, SerialNumber = input.Replacement.SerialNumber,
                        OperatingHours = input.Replacement.InitialOperatingHours, HoursMeasuredAt = request.PerformedAt
                    };
                    item.NewInstallationId = component.Current.Id;
                    foreach (var sensor in component.Sensors) { sensor.Value = null; sensor.MeasuredAt = null; }
                }
                inspection.Items.Add(item);
            }
            s.Inspections.Add(inspection);
            return inspection;
        }, ct);

    public Task<Page<Inspection>> HistoryAsync(Guid equipmentId, int offset, int limit, CancellationToken ct)
    {
        PageBounds(offset, limit);
        return repository.ReadAsync(s =>
        {
            FindEquipment(s, equipmentId);
            var all = s.Inspections.Where(x => x.EquipmentId == equipmentId).OrderByDescending(x => x.PerformedAt).ToList();
            return new Page<Inspection>(all.Skip(offset).Take(limit).ToList(), all.Count, offset, limit);
        }, ct);
    }
    private void ValidateTime(DateTimeOffset value, string field) =>
        Require(value != default && value <= clock.GetUtcNow().AddMinutes(1), field + " обязателен и не может быть в будущем.");
    private bool IsStale(DateTimeOffset? measured) => measured is null ||
        clock.GetUtcNow() - measured > TimeSpan.FromMinutes(Math.Max(1, options.Value.TelemetryMaxAgeMinutes));
    private ResourceStatus ResourceState(decimal? remaining, decimal? norm) => remaining is null || norm is null
        ? ResourceStatus.Unknown : remaining <= 0 ? ResourceStatus.Exhausted :
        remaining / norm <= Math.Clamp(options.Value.WarningRemainingFraction, 0, 1) ? ResourceStatus.Attention : ResourceStatus.Normal;

    public EquipmentCard Card(DatabaseState state, Equipment equipment) => new(equipment.Id, equipment.Name, equipment.Model,
        equipment.SerialNumber, equipment.Characteristics, equipment.Components.Select(c => Card(state, c)).ToList(), equipment.Nameplate, equipment.Passport);
    public ComponentCard Card(DatabaseState state, Component component)
    {
        var current = component.Current;
        decimal? remaining = component.NormLifetimeHours is { } norm && current.OperatingHours is { } hours ? Math.Max(0, norm - hours) : null;
        decimal? baseline = current.LastServiceAt is null ? 0 : current.HoursAtLastService;
        decimal? sinceService = current.OperatingHours is { } total && baseline is { } b ? Math.Max(0, total - b) : null;
        decimal? untilService = component.ServiceIntervalHours is { } interval && sinceService is { } used ? Math.Max(0, interval - used) : null;
        var limits = new List<string>();
        if (component.NormLifetimeHours is null) limits.Add("Не указан нормативный ресурс.");
        if (current.OperatingHours is null) limits.Add("Нет данных о наработке.");
        var stale = IsStale(current.HoursMeasuredAt);
        if (current.OperatingHours is not null && stale) limits.Add("Данные о наработке устарели: оценка относится к последнему измерению.");
        if (component.ServiceIntervalHours is null) limits.Add("Не указан интервал обслуживания.");
        if (current.LastServiceAt is not null && baseline is null) limits.Add("Неизвестна наработка при последнем обслуживании.");
        var resource = new ResourceAssessment(component.NormLifetimeHours, current.OperatingHours, remaining,
            remaining is { } r && component.NormLifetimeHours is { } n ? r / n : null,
            ResourceState(remaining, component.NormLifetimeHours), component.ServiceIntervalHours, sinceService,
            untilService, ResourceState(untilService, component.ServiceIntervalHours), stale, limits.ToArray());
        var equipment=state.Equipment.First(e=>e.Components.Any(c=>c.Id==component.Id));
        var sensors=component.Sensors.Select(sensor=>
        {
            var norm=state.SensorNorms.Find(n=>n.Id==sensor.NormId);
            if(norm is not null&&!ReferenceService.NormMatches(norm,equipment,component,sensor))norm=null;
            var min=norm?.Min;var max=norm?.Max;
            var status=sensor.Value is null||sensor.MeasuredAt is null||norm is null?SensorStatus.Unknown:
                IsStale(sensor.MeasuredAt)?SensorStatus.Stale:
                (min is{} low&&sensor.Value<low)||(max is{} high&&sensor.Value>high)?SensorStatus.OutOfRange:
                (norm.WarningMin is{} warningLow&&sensor.Value<warningLow)||(norm.WarningMax is{} warningHigh&&sensor.Value>warningHigh)?SensorStatus.Attention:SensorStatus.Normal;
            return new SensorAssessment(sensor.Key,sensor.Name,sensor.Unit,min,max,sensor.Value,sensor.MeasuredAt,status,norm?.Id,norm?.Revision,norm?.WarningMin,norm?.WarningMax);
        }).ToList();
        var visual = state.Analyses.Where(a => a.ComponentId == component.Id && a.InstallationId == current.Id)
            .OrderByDescending(a => a.CreatedAt).FirstOrDefault();
        var warnings = new List<string>();
        if (resource.LifetimeStatus == ResourceStatus.Exhausted) warnings.Add("Нормативный ресурс исчерпан: требуется проверка и решение о замене.");
        else if (resource.LifetimeStatus == ResourceStatus.Attention) warnings.Add("Остаток нормативного ресурса ниже порога.");
        if (resource.ServiceStatus == ResourceStatus.Exhausted) warnings.Add("Наступил срок обслуживания по наработке.");
        else if (resource.ServiceStatus == ResourceStatus.Attention) warnings.Add("Приближается обслуживание.");
        foreach (var sensor in sensors.Where(x => x.Status is SensorStatus.OutOfRange or SensorStatus.Attention)) warnings.Add(sensor.Name + ": значение вне уставок.");
        if (visual is not null && visual.VisualStatus != VisualStatus.NoVisibleProblems) warnings.Add(visual.Summary);
        var lastDefectDecision = state.Inspections.OrderByDescending(i => i.PerformedAt).ThenByDescending(i => i.RecordedAt)
            .SelectMany(i => i.Items).FirstOrDefault(x => x.ComponentId == component.Id && x.InstallationId == current.Id &&
                (x.Action == WorkAction.DefectRecorded || x.DefectResolved));
        if (lastDefectDecision?.Action == WorkAction.DefectRecorded)
            warnings.Add("Инженер зарегистрировал дефект: " + lastDefectDecision.Comment);
        return new(component.Id, component.Name, component.Type, current, component.PreviousInstallations, resource, sensors, visual,
            warnings.Count > 0, warnings, RecommendationService.Build(state,equipment,component,resource,sensors,visual));
    }
}
