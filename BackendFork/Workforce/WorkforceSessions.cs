using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using BackendFork.Infrastructure;

namespace BackendFork.Workforce;

public sealed record WorkforceSession(string AccountId,string DisplayName,bool MustChangePin);
public sealed record WorkforceLogin(string Pin,string? EmployeeNumber=null);
public sealed record BootstrapRequest(string FullName,string Pin,string? EmployeeNumber=null);
public sealed record ChangePinRequest(string CurrentPin,string NewPin);

public sealed class WorkforceSessions(WorkforceDb db)
{
    public bool Enabled=>db.Enabled;
    private static string Digest(string token)=>Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(token))).ToLowerInvariant();
    public async Task<JsonObject> Status(CancellationToken ct)
    {
        if(!Enabled)return new(){["enabled"]=false,["configured"]=false};
        await using var c=await db.Open(ct);
        var configured=await WorkforceDb.Scalar(c,null,"SELECT EXISTS(SELECT 1 FROM app.account_roles WHERE role_key='admin')",ct);
        return new(){["enabled"]=true,["configured"]=configured is true,["schemaVersion"]=7};
    }
    private static async Task<JsonObject> Create(System.Data.Common.DbConnection c,System.Data.Common.DbTransaction t,string account,string name,bool change,CancellationToken ct)
    {
        var token=Convert.ToHexString(RandomNumberGenerator.GetBytes(32)); var expiry=DateTimeOffset.UtcNow.AddHours(8);
        await WorkforceDb.Execute(c,t,"INSERT INTO app.sessions(id,account_id,token_hash,expires_at) VALUES(@id::uuid,@account,@hash,@expiry::timestamptz)",ct,("id",Guid.NewGuid()),("account",account),("hash",Digest(token)),("expiry",expiry.ToString("O")));
        return new(){["token"]=token,["expiresAt"]=expiry.ToString("O"),["accountId"]=account,["displayName"]=name,["mustChangePin"]=change};
    }
    public async Task<JsonObject> Bootstrap(BootstrapRequest request,CancellationToken ct)
    {
        ArgonPin.Validate(request.Pin); var name=request.FullName.Trim(); var number=request.EmployeeNumber?.Trim();
        if(string.IsNullOrWhiteSpace(number))number="emp-"+Guid.NewGuid().ToString("N");
        ApiException.Require(name.Length is >=1 and <=200 && number.Length is >=1 and <=64,"Укажите ФИО.");
        var hash=ArgonPin.Hash(request.Pin);
        await using var c=await db.Open(ct); await using var t=await c.BeginTransactionAsync(ct);
        await WorkforceDb.Execute(c,t,"SELECT pg_advisory_xact_lock(74102851)",ct);
        ApiException.Require(await WorkforceDb.Scalar(c,t,"SELECT EXISTS(SELECT 1 FROM app.account_roles WHERE role_key='admin')",ct) is false,"Администратор уже создан.",409);
        await ArgonPin.RequireUnique(c,t,request.Pin,null,ct);
        var id=Guid.NewGuid().ToString("N");
        await WorkforceDb.Execute(c,t,"INSERT INTO app.accounts(id,payload,pin_hash,must_change_pin) VALUES(@id,'{}'::jsonb,@hash,false)",ct,("id",id),("hash",hash));
        await WorkforceDb.Execute(c,t,"INSERT INTO app.employees(account_id,employee_number,full_name) VALUES(@id,@number,@name)",ct,("id",id),("number",number),("name",name));
        await WorkforceDb.Context(c,t,id,"Первичная настройка локального прототипа",Guid.NewGuid().ToString("N"),ct);
        await WorkforceDb.Execute(c,t,"INSERT INTO app.account_roles(account_id,role_key,is_primary,granted_by,grant_reason) VALUES(@id,'admin',true,@id,'Первичная настройка локального прототипа')",ct,("id",id));
        await WorkforceDb.Audit(c,t,id,"admin.bootstrapped",id,ct);
        var result=await Create(c,t,id,name,false,ct); await t.CommitAsync(ct); return result;
    }
    public async Task<JsonObject> Login(WorkforceLogin request,CancellationToken ct)
    {
        ArgonPin.Validate(request.Pin);
        await using var c=await db.Open(ct); await using var t=await c.BeginTransactionAsync(ct);
        var rows=await WorkforceDb.Rows(c,t,"SELECT jsonb_build_object('id',a.id,'name',e.full_name,'hash',a.pin_hash,'mustChangePin',a.must_change_pin,'eligible',a.is_active AND e.employment_status='active' AND (a.locked_until IS NULL OR a.locked_until<=statement_timestamp()))::text FROM app.accounts a JOIN app.employees e ON e.account_id=a.id WHERE (@number IS NULL OR lower(btrim(e.employee_number))=lower(btrim(@number))) FOR UPDATE OF a",ct,("number",string.IsNullOrWhiteSpace(request.EmployeeNumber)?null:request.EmployeeNumber));
        if(string.IsNullOrWhiteSpace(request.EmployeeNumber))
        {
            var matches=rows.Where(r=>r?["eligible"]?.GetValue<bool>() is true && ArgonPin.Verify(request.Pin,r?["hash"]?.GetValue<string>())).ToList();
            if(matches.Count!=1)
            {
                await WorkforceDb.Audit(c,t,null,"auth.login.failed",null,ct);await t.CommitAsync(ct);
                throw new ApiException(matches.Count>1?409:401,matches.Count>1?"Один PIN назначен нескольким сотрудникам. Администратор должен изменить дублирующийся PIN.":"Неверный PIN либо доступ заблокирован.");
            }
            rows=new JsonArray(matches[0]!.DeepClone());
        }
        var row=rows.FirstOrDefault()?.AsObject(); var id=row?["id"]?.GetValue<string>();
        var eligible=row?["eligible"]?.GetValue<bool>() is true;
        var valid=eligible && ArgonPin.Verify(request.Pin,row?["hash"]?.GetValue<string>());
        if(!valid)
        {
            if(id is not null && eligible) await WorkforceDb.Execute(c,t,"UPDATE app.accounts SET failed_pin_attempts=failed_pin_attempts+1,locked_until=CASE WHEN failed_pin_attempts+1>=5 THEN clock_timestamp()+interval '5 minutes' ELSE locked_until END WHERE id=@id",ct,("id",id));
            await WorkforceDb.Audit(c,t,null,"auth.login.failed",null,ct); await t.CommitAsync(ct);
            throw new ApiException(401,"Неверный номер/PIN либо доступ заблокирован.");
        }
        await WorkforceDb.Execute(c,t,"UPDATE app.accounts SET failed_pin_attempts=0,locked_until=NULL,last_login_at=clock_timestamp() WHERE id=@id",ct,("id",id));
        var result=await Create(c,t,id!,row!["name"]!.GetValue<string>(),row["mustChangePin"]!.GetValue<bool>(),ct);
        await WorkforceDb.Audit(c,t,id,"auth.login.succeeded",id,ct); await t.CommitAsync(ct); return result;
    }
    public async Task<WorkforceSession?> Find(string token,CancellationToken ct)
    {
        if(token.Length!=64)return null;
        await using var c=await db.Open(ct);
        var rows=await WorkforceDb.Rows(c,null,"SELECT jsonb_build_object('id',a.id,'name',e.full_name,'change',a.must_change_pin)::text FROM app.sessions s JOIN app.accounts a ON a.id=s.account_id JOIN app.employees e ON e.account_id=a.id WHERE s.token_hash=@hash AND s.revoked_at IS NULL AND s.expires_at>statement_timestamp() AND a.is_active AND e.employment_status='active' AND (a.locked_until IS NULL OR a.locked_until<=statement_timestamp())",ct,("hash",Digest(token)));
        var row=rows.FirstOrDefault(); return row is null?null:new(row["id"]!.GetValue<string>(),row["name"]!.GetValue<string>(),row["change"]!.GetValue<bool>());
    }
    public async Task Revoke(string token,CancellationToken ct)
    {
        await using var c=await db.Open(ct);
        await WorkforceDb.Execute(c,null,"UPDATE app.sessions SET revoked_at=clock_timestamp() WHERE token_hash=@hash AND revoked_at IS NULL",ct,("hash",Digest(token)));
    }
    public async Task<JsonObject> Change(string actor,ChangePinRequest request,CancellationToken ct)
    {
        ArgonPin.Validate(request.CurrentPin);ArgonPin.Validate(request.NewPin);
        ApiException.Require(request.CurrentPin!=request.NewPin,"Новый PIN должен отличаться.");
        var hash=ArgonPin.Hash(request.NewPin);
        await using var c=await db.Open(ct); await using var t=await c.BeginTransactionAsync(ct);
        // All credential changes take this lock before locking an account row.
        await WorkforceDb.Execute(c,t,"SELECT pg_advisory_xact_lock(74102851)",ct);
        var old=await WorkforceDb.Scalar(c,t,"SELECT pin_hash FROM app.accounts WHERE id=@id FOR UPDATE",ct,("id",actor));
        ApiException.Require(ArgonPin.Verify(request.CurrentPin,old as string),"Неверный текущий PIN.",401);
        await ArgonPin.RequireUnique(c,t,request.NewPin,actor,ct);
        await WorkforceDb.Execute(c,t,"UPDATE app.accounts SET pin_hash=@hash,must_change_pin=false,pin_changed_at=clock_timestamp() WHERE id=@id",ct,("hash",hash),("id",actor));
        await WorkforceDb.Audit(c,t,actor,"account.pin.changed",actor,ct);
        var name=(string)(await WorkforceDb.Scalar(c,t,"SELECT full_name FROM app.employees WHERE account_id=@id",ct,("id",actor)))!;
        var result=await Create(c,t,actor,name,false,ct); await t.CommitAsync(ct); return result;
    }
    public async Task<JsonObject> Me(string actor,CancellationToken ct)
    {
        await using var c=await db.Open(ct);
        var rows=await WorkforceDb.Rows(c,null,"SELECT jsonb_build_object('accountId',a.id,'employeeId',e.id,'employeeNumber',e.employee_number,'displayName',e.full_name,'mustChangePin',a.must_change_pin,'roles',(SELECT coalesce(jsonb_agg(role_key),'[]'::jsonb) FROM app.account_roles WHERE account_id=a.id AND revoked_at IS NULL AND valid_from<=statement_timestamp() AND (valid_until IS NULL OR valid_until>statement_timestamp())))::text FROM app.accounts a JOIN app.employees e ON e.account_id=a.id WHERE a.id=@id",ct,("id",actor));
        return rows.FirstOrDefault()?.AsObject() ?? throw new ApiException(401,"Сессия недействительна.");
    }
}
