using System.Data;
using System.Data.Common;
using System.Reflection;
using System.Security.Claims;
using System.Text.Json.Nodes;
using BackendFork.Infrastructure;
using Microsoft.Extensions.Options;

namespace BackendFork.Workforce;

public sealed class WorkforceOptions { public bool Enabled { get; set; } }

// The supplied runtime SQL role intentionally cannot read credentials. This local
// server uses the database owner connection; it enforces API permissions explicitly.
public sealed class WorkforceDb(IOptions<DatabaseOptions> options, IOptions<WorkforceOptions> workforce)
{
    public bool Enabled => workforce.Value.Enabled;
    public async Task<DbConnection> Open(CancellationToken ct)
    {
        ApiException.Require(Enabled, "Серверное администрирование не включено.", 503);
        var factory = (DbProviderFactory)Assembly.Load("Npgsql").GetType("Npgsql.NpgsqlFactory")!.GetField("Instance")!.GetValue(null)!;
        var c = factory.CreateConnection()!;
        c.ConnectionString = options.Value.ConnectionString;
        try { await c.OpenAsync(ct); return c; } catch { await c.DisposeAsync(); throw; }
    }
    public static DbCommand Command(DbConnection c, DbTransaction? t, string sql, params (string Name, object? Value)[] args)
    {
        var command = c.CreateCommand(); command.Transaction = t; command.CommandText = sql;
        foreach (var (name, value) in args)
        {
            var p = command.CreateParameter(); p.ParameterName = name; p.DbType = DbType.String;
            p.Value = value is null ? DBNull.Value : Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture)!;
            command.Parameters.Add(p);
        }
        return command;
    }
    public static async Task<int> Execute(DbConnection c, DbTransaction? t, string sql, CancellationToken ct, params (string Name, object? Value)[] args)
    { await using var cmd = Command(c,t,sql,args); return await cmd.ExecuteNonQueryAsync(ct); }
    public static async Task<object?> Scalar(DbConnection c, DbTransaction? t, string sql, CancellationToken ct, params (string Name, object? Value)[] args)
    { await using var cmd = Command(c,t,sql,args); var v = await cmd.ExecuteScalarAsync(ct); return v is DBNull ? null : v; }
    public static async Task<JsonArray> Rows(DbConnection c, DbTransaction? t, string sql, CancellationToken ct, params (string Name, object? Value)[] args)
    {
        await using var cmd = Command(c,t,sql,args); await using var reader = await cmd.ExecuteReaderAsync(ct);
        var rows = new JsonArray(); while (await reader.ReadAsync(ct)) rows.Add(JsonNode.Parse(reader.GetString(0)));
        return rows;
    }
    public static string Actor(ClaimsPrincipal user) => user.FindFirstValue(ClaimTypes.NameIdentifier) ?? throw new ApiException(401,"Нужен вход.");
    public static async Task Permission(DbConnection c, DbTransaction? t, string actor, string permission, CancellationToken ct)
    {
        var yes = await Scalar(c,t,"SELECT app.account_has_permission_v4(@actor,@permission)",ct,("actor",actor),("permission",permission));
        ApiException.Require(yes is true, "Недостаточно прав: " + permission, 403);
    }
    public static async Task GlobalAdmin(DbConnection c, DbTransaction t, string actor, string permission, CancellationToken ct)
    {
        await Permission(c,t,actor,permission,ct);
        await Permission(c,t,actor,"facilities.access_all",ct);
        // Delegation policy for this prototype: only the active global admin role.
        var yes=await Scalar(c,t,"SELECT EXISTS(SELECT 1 FROM app.account_roles WHERE account_id=@actor AND role_key='admin' AND revoked_at IS NULL AND valid_from<=statement_timestamp() AND (valid_until IS NULL OR valid_until>statement_timestamp()))",ct,("actor",actor));
        ApiException.Require(yes is true,"Изменение доступно глобальному администратору.",403);
    }
    public static async Task Context(DbConnection c, DbTransaction t, string actor, string reason, string requestId, CancellationToken ct)
    {
        reason=reason.Trim(); ApiException.Require(reason.Length is >=3 and <=500,"Укажите причину длиной 3–500 символов.");
        await Execute(c,t,"SELECT set_config('app.actor_account_id',@actor,true),set_config('app.change_reason',@reason,true),set_config('app.request_id',@request,true),set_config('app.event_source','api',true)",ct,("actor",actor),("reason",reason),("request",requestId));
    }
    public static async Task Audit(DbConnection c, DbTransaction t, string? actor, string action, string? resource, CancellationToken ct)
    { await Execute(c,t,"INSERT INTO app.audit_log(actor_id,action_key,resource_id,metadata) VALUES(@actor,@action,@resource,'{}'::jsonb)",ct,("actor",actor),("action",action),("resource",resource)); }
}
