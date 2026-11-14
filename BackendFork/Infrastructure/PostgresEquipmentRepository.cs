using BackendFork.Workforce;
using System.Security.Claims;
using System.Data;
using System.Data.Common;
using System.Globalization;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using BackendFork.Domain;
using Microsoft.Extensions.Options;

namespace BackendFork.Infrastructure;

// Uses ADO.NET so the code can compile offline. Enable UsePostgreSql to include Npgsql at runtime.
public sealed class PostgresEquipmentRepository(
    IOptions<DatabaseOptions> databaseOptions, IOptions<BackendOptions> backendOptions,
    IWebHostEnvironment environment, IHttpContextAccessor http, IOptions<WorkforceOptions> workforce) : IEquipmentRepository
{
    private DbProviderFactory? factory;
    private static readonly JsonSerializerOptions Json = JsonEquipmentRepository.JsonOptions;
    public async Task InitializeAsync()
    {
        var connectionString = databaseOptions.Value.ConnectionString;
        if (string.IsNullOrWhiteSpace(connectionString)) throw new InvalidOperationException("PostgreSQL connection string is missing.");
        try
        {
            factory = (DbProviderFactory?)Assembly.Load("Npgsql").GetType("Npgsql.NpgsqlFactory")?.GetField("Instance")?.GetValue(null);
        }
        catch (FileNotFoundException exception)
        { throw new InvalidOperationException("Npgsql is missing. Run Setup-Postgres.ps1 or build with -p:UsePostgreSql=true.", exception); }
        if (factory is null) throw new InvalidOperationException("Could not load the Npgsql provider.");
        var schema = Path.Combine(AppContext.BaseDirectory,"Database","schema.sql");
        await using (var connection = await OpenAsync(CancellationToken.None))
        {
            var present=await WorkforceDb.Scalar(connection,null,"SELECT to_regclass('app.schema_version') IS NOT NULL",CancellationToken.None);
            if(present is false)
            {
                await using var command = connection.CreateCommand();
                command.CommandText = await File.ReadAllTextAsync(schema);
                await command.ExecuteNonQueryAsync();
            }
        }
        int version;
        await using(var check=await OpenAsync(CancellationToken.None)) version=Convert.ToInt32(await WorkforceDb.Scalar(check,null,"SELECT max(version) FROM app.schema_version",CancellationToken.None));
        if(version<=2) await WriteAsync(state =>
        {
            if (state.Equipment.Count == 0 && state.Account is null && state.Documents.Count == 0 && state.Instructions.Count == 0)
            {
                var oldPath = Path.Combine(environment.ContentRootPath, backendOptions.Value.DataDirectory,"database.json");
                DatabaseState? source = null;
                if (databaseOptions.Value.ImportExistingJson && File.Exists(oldPath))
                    source = JsonSerializer.Deserialize<DatabaseState>(File.ReadAllText(oldPath),Json)
                        ?? throw new InvalidDataException("Existing JSON database is invalid.");
                source ??= backendOptions.Value.SeedDemoData ? DemoData.Create() : new DatabaseState();
                state.Account=source.Account; state.Equipment=source.Equipment; state.Photos=source.Photos;
                state.Analyses=source.Analyses; state.Inspections=source.Inspections; state.Documents=source.Documents;
                state.SensorNorms=source.SensorNorms; state.Instructions=source.Instructions; state.RecommendationRules=source.RecommendationRules;
            }
            ReferenceData.Upgrade(state,backendOptions.Value.SeedDemoData);
            return true;
        });
        if(workforce.Value.Enabled) { await using var c=await OpenAsync(CancellationToken.None); await WorkforceMigration.Apply(c,CancellationToken.None); }
    }
    private string? Actor => workforce.Value.Enabled && http.HttpContext is not null ? http.HttpContext.User.FindFirstValue(ClaimTypes.NameIdentifier) ?? throw new ApiException(401,"Нужен вход.") : null;
    private async Task<DbConnection> OpenAsync(CancellationToken ct)
    {
        var connection=factory?.CreateConnection() ?? throw new InvalidOperationException("PostgreSQL repository is not initialized.");
        connection.ConnectionString=databaseOptions.Value.ConnectionString!;
        try { await connection.OpenAsync(ct); return connection; }
        catch { await connection.DisposeAsync(); throw; }
    }
    public async Task<T> ReadAsync<T>(Func<DatabaseState,T> query,CancellationToken cancellationToken=default)
    {
        await using var connection=await OpenAsync(cancellationToken);
        await using var transaction=await connection.BeginTransactionAsync(IsolationLevel.RepeatableRead,cancellationToken);
        var result=query(await LoadAsync(connection,transaction,cancellationToken,Actor));
        await transaction.CommitAsync(cancellationToken);
        return result;
    }
    public async Task<T> WriteAsync<T>(Func<DatabaseState,T> mutation,CancellationToken cancellationToken=default)
    {
        await using var connection=await OpenAsync(cancellationToken);
        await using var transaction=await connection.BeginTransactionAsync(IsolationLevel.ReadCommitted,cancellationToken);
        // Serialize aggregate writes across API instances. Data mutations and history commit together.
        await ExecuteAsync(connection,transaction,"SELECT pg_advisory_xact_lock(74102851)",cancellationToken);
        if(Actor is {} actor) await WorkforceDb.Context(connection,transaction,actor,"Изменение данных оборудования",http.HttpContext!.TraceIdentifier,cancellationToken);
        var state=await LoadAsync(connection,transaction,cancellationToken,Actor);
        var result=mutation(state);
        await SaveAsync(connection,transaction,state,cancellationToken,workforce.Value.Enabled && Actor is not null);
        await transaction.CommitAsync(cancellationToken);
        return result;
    }
    private static async Task<List<T>> RowsAsync<T>(DbConnection connection,DbTransaction transaction,string sql,CancellationToken ct,string? actor=null)
    {
        await using var command=connection.CreateCommand();command.Transaction=transaction;command.CommandText=actor is null ? sql : "WITH ve AS (SELECT id FROM catalog.equipment WHERE app.account_can_read_equipment_v7(@actor,id)), vc AS (SELECT id FROM catalog.components WHERE equipment_id IN (SELECT id FROM ve)), vi AS (SELECT id FROM catalog.installations WHERE component_id IN (SELECT id FROM vc)), vr AS (SELECT id FROM reports.inspections WHERE equipment_id IN (SELECT id FROM ve) AND (app.account_has_permission_v4(@actor,'inspections.read_all') OR (author_id=@actor AND app.account_has_permission_v4(@actor,'inspections.read_own')))) " + sql;
        if(actor is not null){var p=command.CreateParameter();p.ParameterName="actor";p.DbType=DbType.String;p.Value=actor;command.Parameters.Add(p);}
        await using var reader=await command.ExecuteReaderAsync(ct);var result=new List<T>();
        while(await reader.ReadAsync(ct))result.Add(JsonSerializer.Deserialize<T>(reader.GetString(0),Json)!);
        return result;
    }
    private sealed record Owned<T>(Guid OwnerId,T Value);
    private sealed record Hours(Guid InstallationId,decimal Value,DateTimeOffset MeasuredAt);
    private sealed record Reading(Guid ComponentId,string SensorKey,decimal Value,DateTimeOffset MeasuredAt);
    private sealed record ResourceNorm(Guid ComponentId,decimal? LifetimeHours,decimal? ServiceIntervalHours);
    private static async Task<DatabaseState> LoadAsync(DbConnection c,DbTransaction t,CancellationToken ct,string? actor)
    {
        var state=new DatabaseState();
        state.Account=actor is null ? (await RowsAsync<PinAccount>(c,t,"SELECT payload::text FROM app.accounts WHERE payload ? 'salt' AND payload ? 'hash' ORDER BY id",ct)).FirstOrDefault() : null;
        state.Equipment=await RowsAsync<Equipment>(c,t,actor is null ? "SELECT payload::text FROM catalog.equipment ORDER BY id" : "SELECT (payload || jsonb_build_object('name',name,'model',model,'serialNumber',serial_number,'qrCode',qr_code))::text FROM catalog.equipment WHERE id IN (SELECT id FROM ve) ORDER BY id",ct,actor);
        var equipment=state.Equipment.ToDictionary(x=>x.Id);
        foreach(var row in await RowsAsync<Owned<NameplateInfo>>(c,t,"SELECT jsonb_build_object('ownerId',equipment_id,'value',payload)::text FROM catalog.nameplates"+(actor is null?"":" WHERE equipment_id IN (SELECT id FROM ve)"),ct,actor))
            equipment[row.OwnerId].Nameplate=row.Value;
        foreach(var row in await RowsAsync<Owned<PassportInfo>>(c,t,"SELECT jsonb_build_object('ownerId',equipment_id,'value',payload)::text FROM catalog.passports"+(actor is null?"":" WHERE equipment_id IN (SELECT id FROM ve)"),ct,actor))
            equipment[row.OwnerId].Passport=row.Value;
        var components=new Dictionary<Guid,Component>();
        foreach(var row in await RowsAsync<Owned<Component>>(c,t,"SELECT jsonb_build_object('ownerId',equipment_id,'value',payload)::text FROM catalog.components"+(actor is null?"":" WHERE id IN (SELECT id FROM vc)"),ct,actor))
        { components[row.Value.Id]=row.Value;equipment[row.OwnerId].Components.Add(row.Value); }
        var installations=new Dictionary<Guid,Installation>();
        foreach(var row in await RowsAsync<Owned<Installation>>(c,t,"SELECT jsonb_build_object('ownerId',component_id,'value',payload)::text FROM catalog.installations"+(actor is null?"":" WHERE id IN (SELECT id FROM vi)")+" ORDER BY installed_at",ct,actor))
        {
            installations[row.Value.Id]=row.Value;
            if(row.Value.RemovedAt is null)components[row.OwnerId].Current=row.Value;
            else components[row.OwnerId].PreviousInstallations.Add(row.Value);
        }
        foreach(var row in await RowsAsync<Hours>(c,t,
            "SELECT DISTINCT ON (installation_id) jsonb_build_object('installationId',installation_id,'value',value,'measuredAt',measured_at)::text FROM telemetry.operating_hours"+(actor is null?"":" WHERE installation_id IN (SELECT id FROM vi)")+" ORDER BY installation_id,measured_at DESC",ct,actor))
        { installations[row.InstallationId].OperatingHours=row.Value;installations[row.InstallationId].HoursMeasuredAt=row.MeasuredAt; }
        foreach(var row in await RowsAsync<ResourceNorm>(c,t,
            "SELECT jsonb_build_object('componentId',component_id,'lifetimeHours',lifetime_hours,'serviceIntervalHours',service_interval_hours)::text FROM standards.resource_norms"+(actor is null?"":" WHERE component_id IN (SELECT id FROM vc)"),ct,actor))
        { components[row.ComponentId].NormLifetimeHours=row.LifetimeHours;components[row.ComponentId].ServiceIntervalHours=row.ServiceIntervalHours; }
        state.SensorNorms=await RowsAsync<SensorNorm>(c,t,"SELECT (payload || jsonb_build_object('min',min_value,'max',max_value,'warningMin',warning_min,'warningMax',warning_max,'revision',revision))::text FROM standards.sensor_norms",ct,actor);
        foreach(var row in await RowsAsync<Owned<Sensor>>(c,t,
            "SELECT jsonb_build_object('ownerId',component_id,'value',payload || jsonb_build_object('normId',norm_id,'unit',unit,'kind',sensor_kind))::text FROM telemetry.sensors WHERE active"+(actor is null?"":" AND component_id IN (SELECT id FROM vc)"),ct,actor))
            components[row.OwnerId].Sensors.Add(row.Value);
        foreach(var row in await RowsAsync<Reading>(c,t,
            "SELECT DISTINCT ON (r.component_id,r.sensor_key) jsonb_build_object('componentId',r.component_id,'sensorKey',r.sensor_key,'value',r.value,'measuredAt',r.measured_at)::text FROM telemetry.readings r JOIN catalog.installations i ON i.id=r.installation_id AND i.removed_at IS NULL JOIN telemetry.sensors s ON s.component_id=r.component_id AND s.sensor_key=r.sensor_key AND s.active AND s.unit=r.unit "+(actor is null?"":" WHERE r.component_id IN (SELECT id FROM vc)")+" ORDER BY r.component_id,r.sensor_key,r.measured_at DESC",ct,actor))
        { var sensor=components[row.ComponentId].Sensors.Find(x=>x.Key==row.SensorKey)!;sensor.Value=row.Value;sensor.MeasuredAt=row.MeasuredAt; }
        state.Photos=await RowsAsync<Photo>(c,t,"SELECT payload::text FROM storage.photos p"+(actor is null?"":" WHERE ((p.payload->>'authorId'= @actor AND NOT EXISTS(SELECT 1 FROM reports.inspection_photos ip WHERE ip.photo_id=p.id) AND NOT EXISTS(SELECT 1 FROM ai.analyses a WHERE a.photo_id=p.id)) OR EXISTS(SELECT 1 FROM reports.inspection_photos ip WHERE ip.photo_id=p.id AND ip.inspection_id IN (SELECT id FROM vr)) OR EXISTS(SELECT 1 FROM ai.analyses a WHERE a.photo_id=p.id AND a.equipment_id IN (SELECT id FROM ve)))"),ct,actor);
        state.Documents=await RowsAsync<StoredDocument>(c,t,"SELECT payload::text FROM storage.documents"+(actor is null?"":" WHERE equipment_id IS NULL OR equipment_id IN (SELECT id FROM ve)"),ct,actor);
        state.Instructions=await RowsAsync<Instruction>(c,t,"SELECT payload::text FROM knowledge.instructions",ct,actor);
        state.RecommendationRules=await RowsAsync<RecommendationRule>(c,t,"SELECT payload::text FROM knowledge.recommendation_rules",ct,actor);
        state.Analyses=await RowsAsync<Analysis>(c,t,"SELECT payload::text FROM ai.analyses"+(actor is null?"":" WHERE equipment_id IN (SELECT id FROM ve)"),ct,actor);
        state.Inspections=await RowsAsync<Inspection>(c,t,"SELECT payload::text FROM reports.inspections"+(actor is null?"":" WHERE id IN (SELECT id FROM vr)"),ct,actor);
        return state;
    }
    private static string Payload(object value,params string[] exclude)
    {
        var json=JsonSerializer.SerializeToNode(value,Json)!.AsObject();
        foreach(var key in exclude)json.Remove(key);
        return json.ToJsonString(Json);
    }
    private static async Task ExecuteAsync(DbConnection c,DbTransaction t,string sql,CancellationToken ct,params (string Name,object? Value)[] parameters)
    {
        await using var command=c.CreateCommand();command.Transaction=t;command.CommandText=sql;
        foreach(var (name,value) in parameters)
        {
            var parameter=command.CreateParameter();parameter.ParameterName=name;parameter.DbType=DbType.String;
            parameter.Value=value switch
            {
                null => DBNull.Value, DateTimeOffset date=>date.ToUniversalTime().ToString("O"),
                bool b=>b?"true":"false", IFormattable f=>f.ToString(null,CultureInfo.InvariantCulture), _=>value.ToString()!
            };
            command.Parameters.Add(parameter);
        }
        await command.ExecuteNonQueryAsync(ct);
    }
    private static async Task SaveAsync(DbConnection c,DbTransaction t,DatabaseState state,CancellationToken ct,bool integrated=false)
    {
        if(state.Account is{} account)await ExecuteAsync(c,t,
            "INSERT INTO app.accounts(id,payload) VALUES(@id,@json::jsonb) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",ct,
            ("id",account.Id),("json",Payload(account)));
        foreach(var e in state.Equipment)
        {
            await ExecuteAsync(c,t,"INSERT INTO catalog.equipment(id,qr_code,serial_number,model,name,payload) VALUES(@id::uuid,@qr,@serial,@model,@name,@json::jsonb) ON CONFLICT(id) DO UPDATE SET qr_code=excluded.qr_code,serial_number=excluded.serial_number,model=excluded.model,name=excluded.name,payload=excluded.payload",ct,
                ("id",e.Id),("qr",e.QrCode),("serial",e.SerialNumber),("model",e.Model),("name",e.Name),("json",Payload(e,"components","nameplate","passport")));
            await ExecuteAsync(c,t,"INSERT INTO catalog.nameplates(equipment_id,payload) VALUES(@id::uuid,@json::jsonb) ON CONFLICT(equipment_id) DO UPDATE SET payload=excluded.payload",ct,("id",e.Id),("json",Payload(e.Nameplate)));
            await ExecuteAsync(c,t,"INSERT INTO catalog.passports(equipment_id,payload) VALUES(@id::uuid,@json::jsonb) ON CONFLICT(equipment_id) DO UPDATE SET payload=excluded.payload",ct,("id",e.Id),("json",Payload(e.Passport)));
        }
        foreach(var photo in state.Photos)await ExecuteAsync(c,t,"INSERT INTO storage.photos(id,payload) VALUES(@id::uuid,@json::jsonb) ON CONFLICT(id) DO NOTHING",ct,("id",photo.Id),("json",Payload(photo)));
        foreach(var document in state.Documents)await ExecuteAsync(c,t,
            "INSERT INTO storage.documents(id,equipment_id,category,payload) VALUES(@id::uuid,@equipment::uuid,@category,@json::jsonb) ON CONFLICT(id) DO NOTHING",ct,
            ("id",document.Id),("equipment",document.EquipmentId),("category",document.Category.ToString()),("json",Payload(document)));
        foreach(var norm in state.SensorNorms)await ExecuteAsync(c,t,
            "INSERT INTO standards.sensor_norms(id,name,sensor_kind,unit,min_value,max_value,warning_min,warning_max,revision,is_demo,source_document_id,payload) VALUES(@id::uuid,@name,@kind,@unit,@min::numeric,@max::numeric,@warnMin::numeric,@warnMax::numeric,@revision::integer,@demo::boolean,@source::uuid,@json::jsonb) ON CONFLICT(id) DO UPDATE SET name=excluded.name,sensor_kind=excluded.sensor_kind,unit=excluded.unit,min_value=excluded.min_value,max_value=excluded.max_value,warning_min=excluded.warning_min,warning_max=excluded.warning_max,revision=excluded.revision,is_demo=excluded.is_demo,source_document_id=excluded.source_document_id,payload=excluded.payload",ct,
            ("id",norm.Id),("name",norm.Name),("kind",norm.SensorKind),("unit",norm.Unit),("min",norm.Min),("max",norm.Max),
            ("warnMin",norm.WarningMin),("warnMax",norm.WarningMax),("revision",norm.Revision),("demo",norm.IsDemo),("source",norm.SourceDocumentId),("json",Payload(norm)));
        foreach(var e in state.Equipment)
        foreach(var component in e.Components)
        {
            await ExecuteAsync(c,t,"INSERT INTO catalog.components(id,equipment_id,name,component_type,payload) VALUES(@id::uuid,@owner::uuid,@name,@type,@json::jsonb) ON CONFLICT(id) DO UPDATE SET name=excluded.name,component_type=excluded.component_type,payload=excluded.payload",ct,
                ("id",component.Id),("owner",e.Id),("name",component.Name),("type",component.Type),("json",Payload(component,"current","previousInstallations","sensors","normLifetimeHours","serviceIntervalHours")));
            foreach(var installation in component.PreviousInstallations.Append(component.Current))
            {
                await ExecuteAsync(c,t,"INSERT INTO catalog.installations(id,component_id,installed_at,removed_at,payload) VALUES(@id::uuid,@owner::uuid,@installed::timestamptz,@removed::timestamptz,@json::jsonb) ON CONFLICT(id) DO UPDATE SET removed_at=excluded.removed_at,payload=excluded.payload",ct,
                    ("id",installation.Id),("owner",component.Id),("installed",installation.InstalledAt),("removed",installation.RemovedAt),("json",Payload(installation,"operatingHours","hoursMeasuredAt")));
                if(installation.OperatingHours is{} hours && installation.HoursMeasuredAt is{} measured)
                    await ExecuteAsync(c,t,"INSERT INTO telemetry.operating_hours(installation_id,measured_at,value) VALUES(@id::uuid,@measured::timestamptz,@value::numeric) ON CONFLICT DO NOTHING",ct,
                        ("id",installation.Id),("measured",measured),("value",hours));
            }
            await ExecuteAsync(c,t,"INSERT INTO standards.resource_norms(component_id,lifetime_hours,service_interval_hours) VALUES(@id::uuid,@lifetime::numeric,@service::numeric) ON CONFLICT(component_id) DO UPDATE SET lifetime_hours=excluded.lifetime_hours,service_interval_hours=excluded.service_interval_hours",ct,
                ("id",component.Id),("lifetime",component.NormLifetimeHours),("service",component.ServiceIntervalHours));
            await ExecuteAsync(c,t,"UPDATE telemetry.sensors SET active=false WHERE component_id=@id::uuid",ct,("id",component.Id));
            foreach(var sensor in component.Sensors)
            {
                await ExecuteAsync(c,t,"INSERT INTO telemetry.sensors(component_id,sensor_key,norm_id,sensor_kind,unit,active,payload) VALUES(@owner::uuid,@key,@norm::uuid,@kind,@unit,true,@json::jsonb) ON CONFLICT(component_id,sensor_key) DO UPDATE SET norm_id=excluded.norm_id,sensor_kind=excluded.sensor_kind,unit=excluded.unit,active=true,payload=excluded.payload",ct,
                    ("owner",component.Id),("key",sensor.Key),("norm",sensor.NormId),("kind",sensor.Kind),("unit",sensor.Unit),("json",Payload(sensor,"min","max","value","measuredAt")));
                if(sensor.Value is{} value && sensor.MeasuredAt is{} date)
                    await ExecuteAsync(c,t,"INSERT INTO telemetry.readings(component_id,sensor_key,installation_id,measured_at,value,unit) VALUES(@owner::uuid,@key,@installation::uuid,@date::timestamptz,@value::numeric,@unit) ON CONFLICT DO NOTHING",ct,
                        ("owner",component.Id),("key",sensor.Key),("installation",component.Current.Id),("date",date),("value",value),("unit",sensor.Unit));
            }
        }
        foreach(var instruction in state.Instructions)await ExecuteAsync(c,t,
            "INSERT INTO knowledge.instructions(id,title,revision,document_id,payload) VALUES(@id::uuid,@title,@revision::integer,@doc::uuid,@json::jsonb) ON CONFLICT(id) DO UPDATE SET title=excluded.title,revision=excluded.revision,document_id=excluded.document_id,payload=excluded.payload",ct,
            ("id",instruction.Id),("title",instruction.Title),("revision",instruction.Revision),("doc",instruction.DocumentId),("json",Payload(instruction)));
        foreach(var rule in state.RecommendationRules)await ExecuteAsync(c,t,
            "INSERT INTO knowledge.recommendation_rules(id,trigger,instruction_id,payload) VALUES(@id::uuid,@trigger,@instruction::uuid,@json::jsonb) ON CONFLICT(id) DO UPDATE SET trigger=excluded.trigger,instruction_id=excluded.instruction_id,payload=excluded.payload",ct,
            ("id",rule.Id),("trigger",rule.Trigger.ToString()),("instruction",rule.InstructionId),("json",Payload(rule)));
        foreach(var analysis in state.Analyses)await ExecuteAsync(c,t,
            "INSERT INTO ai.analyses(id,equipment_id,component_id,installation_id,photo_id,created_at,payload) VALUES(@id::uuid,@equipment::uuid,@component::uuid,@installation::uuid,@photo::uuid,@date::timestamptz,@json::jsonb) ON CONFLICT(id) DO NOTHING",ct,
            ("id",analysis.Id),("equipment",analysis.EquipmentId),("component",analysis.ComponentId),("installation",analysis.InstallationId),("photo",analysis.PhotoId),("date",analysis.CreatedAt),("json",Payload(analysis)));
        foreach(var report in state.Inspections)
        {
            await ExecuteAsync(c,t,"INSERT INTO reports.inspections(id,equipment_id,author_id,performed_at,payload) VALUES(@id::uuid,@equipment::uuid,@author,@date::timestamptz,@json::jsonb) ON CONFLICT(id) DO NOTHING",ct,
                ("id",report.Id),("equipment",report.EquipmentId),("author",report.AuthorId),("date",report.PerformedAt),("json",Payload(report)));
            foreach(var item in report.Items)
            {
                await ExecuteAsync(c,t,"INSERT INTO reports.inspection_items(inspection_id,component_id,installation_id,new_installation_id,analysis_id,action,payload) VALUES(@report::uuid,@component::uuid,@installation::uuid,@new::uuid,@analysis::uuid,@action,@json::jsonb) ON CONFLICT DO NOTHING",ct,
                    ("report",report.Id),("component",item.ComponentId),("installation",item.InstallationId),("new",item.NewInstallationId),("analysis",item.AnalysisId),("action",item.Action.ToString()),("json",Payload(item)));
                if(integrated && item.FindingId is not null && item.DefectTypeCode is not null)
                    await ExecuteAsync(c,t,"INSERT INTO reports.inspection_findings(id,inspection_id,component_id,installation_id,defect_type_code,title,description,detected_by_account_id,detected_at,source) VALUES(@id::uuid,@report::uuid,@component::uuid,@installation::uuid,@code,@title,@description,@actor,@date::timestamptz,'manual') ON CONFLICT(id) DO NOTHING",ct,
                        ("id",item.FindingId),("report",report.Id),("component",item.ComponentId),("installation",item.InstallationId),("code",item.DefectTypeCode),("title",DefectCatalog.All.Single(x=>x.Code==item.DefectTypeCode).Name),("description",item.Comment),("actor",report.AuthorId),("date",report.PerformedAt));
                foreach(var photoId in item.PhotoIds)await ExecuteAsync(c,t,
                    "INSERT INTO reports.inspection_photos(inspection_id,component_id,photo_id) VALUES(@report::uuid,@component::uuid,@photo::uuid) ON CONFLICT DO NOTHING",ct,
                    ("report",report.Id),("component",item.ComponentId),("photo",photoId));
            }
        }
    }
}
