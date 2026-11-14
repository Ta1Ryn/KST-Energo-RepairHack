using System.Data.Common;
using System.Text.Json.Nodes;
using BackendFork.Infrastructure;
using BackendFork.Workforce;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

public sealed record EmployeeWrite(string FullName,string? Pin,string? EmployeeNumber=null,string Role="engineer",Guid? DepartmentId=null,string? PositionTitle=null,string Reason="Создание сотрудника в локальном прототипе");
public sealed record EmployeeUpdate(string FullName,string EmploymentStatus,string Reason,Guid? DepartmentId=null,string? PositionTitle=null,DateOnly? TerminatedOn=null);
public sealed record PinReset(string Pin,string Reason);
public sealed record FacilityWrite(string Code,string Name,string Type="workshop",Guid? ParentId=null);
public sealed record DepartmentWrite(string Code,string Name,Guid? ParentId=null);
public sealed record DepartmentFacility(Guid DepartmentId,Guid FacilityId,string Reason);
public sealed record EquipmentLocation(Guid? FacilityId,string Reason);
public sealed record AccessWrite(Guid EmployeeId,Guid? EquipmentId,Guid? FacilityId,bool IncludeDescendants,string Reason,DateTimeOffset? ValidUntil=null);
public sealed record RevokeAccess(string Reason);

[ApiController,Authorize,Route("api/admin")]
public sealed class AdminController(WorkforceDb db):ControllerBase
{
    private string Actor=>WorkforceDb.Actor(User);
    private async Task Setup(DbConnection c,DbTransaction t,string permission,string reason,CancellationToken ct)
    {await WorkforceDb.GlobalAdmin(c,t,Actor,permission,ct);await WorkforceDb.Context(c,t,Actor,reason,HttpContext.TraceIdentifier,ct);}
    private const string EmployeeJson="jsonb_build_object('id',e.id,'accountId',e.account_id,'employeeNumber',e.employee_number,'fullName',e.full_name,'positionTitle',e.position_title,'departmentId',e.department_id,'employmentStatus',e.employment_status,'roles',(SELECT coalesce(jsonb_agg(role_key),'[]'::jsonb) FROM app.account_roles r WHERE r.account_id=e.account_id AND r.revoked_at IS NULL AND r.valid_from<=statement_timestamp() AND (r.valid_until IS NULL OR r.valid_until>statement_timestamp())))::text";
    [HttpGet("employees")]
    public async Task<JsonArray> Employees(CancellationToken ct)
    {
        await using var c=await db.Open(ct);
        return await WorkforceDb.Rows(c,null,"SELECT "+EmployeeJson+" FROM app.employees e WHERE app.account_can_read_employee_v7(@actor,e.id) ORDER BY e.employee_number LIMIT 500",ct,("actor",Actor));
    }
    [HttpPost("employees")]
    public async Task<JsonObject> Create(EmployeeWrite request,CancellationToken ct)
    {
        var number=request.EmployeeNumber?.Trim();var name=request.FullName.Trim();
        if(string.IsNullOrWhiteSpace(number))number="emp-"+Guid.NewGuid().ToString("N");
        ApiException.Require(number.Length is >=1 and <=64 && name.Length is >=1 and <=200,"Укажите табельный номер и ФИО.");
        ApiException.Require(request.Role is "engineer" or "supervisor","Допустимые роли: engineer, supervisor.");
        var hash=request.Pin is null?null:ArgonPin.Hash(request.Pin);
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);
        await Setup(c,t,"employees.manage",request.Reason,ct);
        string? account=null;
        if(hash is not null)
        {
            await ArgonPin.RequireUnique(c,t,request.Pin!,null,ct);
            await WorkforceDb.GlobalAdmin(c,t,Actor,"accounts.manage",ct);await WorkforceDb.GlobalAdmin(c,t,Actor,"roles.manage",ct);
            account=Guid.NewGuid().ToString("N");
            await WorkforceDb.Execute(c,t,"INSERT INTO app.accounts(id,payload,pin_hash,must_change_pin,created_by) VALUES(@id,'{}'::jsonb,@hash,true,@actor)",ct,("id",account),("hash",hash),("actor",Actor));
        }
        var id=(Guid)(await WorkforceDb.Scalar(c,t,"INSERT INTO app.employees(account_id,employee_number,full_name,department_id,position_title) VALUES(@account,@number,@name,@department::uuid,@position) RETURNING id",ct,("account",account),("number",number),("name",name),("department",request.DepartmentId),("position",request.PositionTitle)))!;
        if(account is not null)await WorkforceDb.Execute(c,t,"INSERT INTO app.account_roles(account_id,role_key,is_primary,granted_by,grant_reason) VALUES(@account,@role,true,@actor,@reason)",ct,("account",account),("role",request.Role),("actor",Actor),("reason",request.Reason));
        await WorkforceDb.Audit(c,t,Actor,"employee.created",id.ToString(),ct);await t.CommitAsync(ct);
        return new(){["id"]=id.ToString(),["accountId"]=account,["mustChangePin"]=account is not null};
    }
    private async Task ProtectAdmin(DbConnection c,DbTransaction t,Guid employee,CancellationToken ct)
    {
        var admin=await WorkforceDb.Scalar(c,t,"SELECT EXISTS(SELECT 1 FROM app.employees e JOIN app.account_roles r ON r.account_id=e.account_id WHERE e.id=@id::uuid AND r.role_key='admin' AND r.revoked_at IS NULL)",ct,("id",employee));
        ApiException.Require(admin is false,"Управление администраторами через эту форму отключено; свой PIN меняется в настройках.",403);
    }
    [HttpPut("employees/{id:guid}")]
    public async Task<IActionResult> Update(Guid id,EmployeeUpdate request,CancellationToken ct)
    {
        ApiException.Require(request.FullName.Trim().Length is >=1 and <=200,"Укажите ФИО.");
        ApiException.Require(request.EmploymentStatus is "active" or "suspended" or "terminated","Неизвестный статус.");
        ApiException.Require(request.EmploymentStatus!="terminated" || request.TerminatedOn is not null,"Укажите фактическую дату увольнения.");
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);
        await Setup(c,t,"employees.manage",request.Reason,ct);await ProtectAdmin(c,t,id,ct);
        var rows=await WorkforceDb.Execute(c,t,"UPDATE app.employees SET full_name=@name,employment_status=@status,department_id=@department::uuid,position_title=@position,terminated_on=@date::date WHERE id=@id::uuid",ct,("name",request.FullName.Trim()),("status",request.EmploymentStatus),("department",request.DepartmentId),("position",request.PositionTitle),("date",request.TerminatedOn?.ToString("yyyy-MM-dd")),("id",id));
        ApiException.Require(rows==1,"Сотрудник не найден.",404);
        if(request.EmploymentStatus=="active") await WorkforceDb.Execute(c,t,"UPDATE app.accounts SET is_active=true WHERE id=(SELECT account_id FROM app.employees WHERE id=@id::uuid)",ct,("id",id));
        await WorkforceDb.Audit(c,t,Actor,"employee.updated",id.ToString(),ct);await t.CommitAsync(ct);return NoContent();
    }
    [HttpPost("employees/{id:guid}/pin")]
    public async Task<IActionResult> ResetPin(Guid id,PinReset request,CancellationToken ct)
    {
        var hash=ArgonPin.Hash(request.Pin);
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);
        await Setup(c,t,"accounts.reset_pin",request.Reason,ct);await ProtectAdmin(c,t,id,ct);
        var account=await WorkforceDb.Scalar(c,t,"SELECT account_id FROM app.employees WHERE id=@id::uuid",ct,("id",id));
        await ArgonPin.RequireUnique(c,t,request.Pin,account as string,ct);
        var rows=await WorkforceDb.Execute(c,t,"UPDATE app.accounts SET pin_hash=@hash,must_change_pin=true,failed_pin_attempts=0,locked_until=NULL WHERE id=(SELECT account_id FROM app.employees WHERE id=@id::uuid)",ct,("hash",hash),("id",id));
        ApiException.Require(rows==1,"У сотрудника нет учётной записи.",404);
        await WorkforceDb.Audit(c,t,Actor,"account.pin.reset",id.ToString(),ct);await t.CommitAsync(ct);return NoContent();
    }
    [HttpGet("facilities")]
    public async Task<JsonArray> Facilities(CancellationToken ct)
    {
        await using var c=await db.Open(ct);await WorkforceDb.Permission(c,null,Actor,"facilities.read_assigned",ct);
        return await WorkforceDb.Rows(c,null,"SELECT jsonb_build_object('id',id,'code',facility_code,'name',name,'type',facility_type,'parentId',parent_facility_id,'active',is_active)::text FROM app.facilities WHERE app.account_can_access_facility_v6(@actor,id) ORDER BY name LIMIT 500",ct,("actor",Actor));
    }
    [HttpPost("facilities")]
    public async Task<JsonObject> CreateFacility(FacilityWrite request,CancellationToken ct)
    {
        ApiException.Require(request.Code.Trim().Length is >=1 and <=64 && request.Name.Trim().Length is >=1 and <=200,"Укажите код и название объекта.");
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);await Setup(c,t,"facilities.manage","Создание объекта",ct);
        var id=Guid.NewGuid();await WorkforceDb.Execute(c,t,"INSERT INTO app.facilities(id,facility_code,name,facility_type,parent_facility_id,created_by) VALUES(@id::uuid,@code,@name,@type,@parent::uuid,@actor)",ct,("id",id),("code",request.Code.Trim()),("name",request.Name.Trim()),("type",request.Type),("parent",request.ParentId),("actor",Actor));
        await WorkforceDb.Audit(c,t,Actor,"facility.created",id.ToString(),ct);await t.CommitAsync(ct);return new(){["id"]=id.ToString()};
    }
    [HttpGet("departments")]
    public async Task<JsonArray> Departments(CancellationToken ct)
    {
        await using var c=await db.Open(ct);await WorkforceDb.Permission(c,null,Actor,"departments.read",ct);
        return await WorkforceDb.Rows(c,null,"SELECT jsonb_build_object('id',id,'code',department_code,'name',name,'parentId',parent_department_id)::text FROM app.departments ORDER BY name LIMIT 500",ct);
    }
    [HttpPost("departments")]
    public async Task<JsonObject> CreateDepartment(DepartmentWrite request,CancellationToken ct)
    {
        ApiException.Require(request.Code.Trim().Length is >=1 and <=64 && request.Name.Trim().Length is >=1 and <=200,"Укажите код и название подразделения.");
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);await Setup(c,t,"departments.manage","Создание подразделения",ct);
        var id=Guid.NewGuid();await WorkforceDb.Execute(c,t,"INSERT INTO app.departments(id,department_code,name,parent_department_id,created_by) VALUES(@id::uuid,@code,@name,@parent::uuid,@actor)",ct,("id",id),("code",request.Code.Trim()),("name",request.Name.Trim()),("parent",request.ParentId),("actor",Actor));
        await WorkforceDb.Audit(c,t,Actor,"department.created",id.ToString(),ct);await t.CommitAsync(ct);return new(){["id"]=id.ToString()};
    }
    [HttpPost("department-facilities")]
    public async Task<IActionResult> Link(DepartmentFacility request,CancellationToken ct)
    {
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);await Setup(c,t,"departments.manage",request.Reason,ct);await WorkforceDb.GlobalAdmin(c,t,Actor,"facilities.manage",ct);
        await WorkforceDb.Execute(c,t,"INSERT INTO app.department_facilities(department_id,facility_id,assigned_by) VALUES(@department::uuid,@facility::uuid,@actor) ON CONFLICT DO NOTHING",ct,("department",request.DepartmentId),("facility",request.FacilityId),("actor",Actor));
        await WorkforceDb.Audit(c,t,Actor,"department.facility.linked",request.DepartmentId.ToString(),ct);await t.CommitAsync(ct);return NoContent();
    }
    [HttpPut("equipment/{id:guid}/facility")]
    public async Task<IActionResult> Location(Guid id,EquipmentLocation request,CancellationToken ct)
    {
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);await Setup(c,t,"equipment.assign_facility",request.Reason,ct);
        var rows=await WorkforceDb.Execute(c,t,"UPDATE catalog.equipment SET facility_id=@facility::uuid WHERE id=@id::uuid AND app.account_can_read_equipment_v7(@actor,id)",ct,("facility",request.FacilityId),("id",id),("actor",Actor));
        ApiException.Require(rows==1,"Оборудование не найдено или недоступно.",404);await t.CommitAsync(ct);return NoContent();
    }
    [HttpGet("access")]
    public async Task<JsonArray> Access(CancellationToken ct)
    {
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);await WorkforceDb.GlobalAdmin(c,t,Actor,"facilities.assign_accounts",ct);
        var rows=await WorkforceDb.Rows(c,t,"SELECT jsonb_build_object('id',g.id,'employeeId',g.employee_id,'employeeName',e.full_name,'equipmentId',g.equipment_id,'facilityId',g.facility_id,'targetName',coalesce(eq.name,f.name),'includeDescendants',g.include_descendants,'active',g.is_active,'validUntil',g.valid_until)::text FROM app.employee_object_access g JOIN app.employees e ON e.id=g.employee_id LEFT JOIN catalog.equipment eq ON eq.id=g.equipment_id LEFT JOIN app.facilities f ON f.id=g.facility_id ORDER BY g.granted_at DESC LIMIT 500",ct);
        await t.CommitAsync(ct);return rows;
    }
    [HttpPost("access")]
    public async Task<JsonObject> Grant(AccessWrite request,CancellationToken ct)
    {
        ApiException.Require((request.EquipmentId is null)!=(request.FacilityId is null),"Выберите оборудование либо объект.");
        ApiException.Require(request.EquipmentId is null || !request.IncludeDescendants,"Для оборудования дочерние объекты не применяются.");
        ApiException.Require(request.ValidUntil is null || request.ValidUntil>DateTimeOffset.UtcNow,"Срок доступа уже истёк.");
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);await Setup(c,t,"facilities.assign_accounts",request.Reason,ct);
        await WorkforceDb.Execute(c,t,"SELECT pg_advisory_xact_lock(74102853)",ct);
        var existing=await WorkforceDb.Scalar(c,t,"SELECT id FROM app.employee_object_access WHERE employee_id=@employee::uuid AND equipment_id IS NOT DISTINCT FROM @equipment::uuid AND facility_id IS NOT DISTINCT FROM @facility::uuid FOR UPDATE",ct,("employee",request.EmployeeId),("equipment",request.EquipmentId),("facility",request.FacilityId));
        var id=existing ?? await WorkforceDb.Scalar(c,t,"INSERT INTO app.employee_object_access(employee_id,equipment_id,facility_id,include_descendants,valid_from,valid_until) VALUES(@employee::uuid,@equipment::uuid,@facility::uuid,@children::boolean,statement_timestamp(),@until::timestamptz) RETURNING id",ct,("employee",request.EmployeeId),("equipment",request.EquipmentId),("facility",request.FacilityId),("children",request.IncludeDescendants),("until",request.ValidUntil?.ToString("O")));
        if(existing is not null)await WorkforceDb.Execute(c,t,"UPDATE app.employee_object_access SET is_active=true,include_descendants=@children::boolean,valid_from=statement_timestamp(),valid_until=@until::timestamptz WHERE id=@id::uuid",ct,("id",id),("children",request.IncludeDescendants),("until",request.ValidUntil?.ToString("O")));
        await t.CommitAsync(ct);return new(){["id"]=id!.ToString()};
    }
    [HttpPost("access/{id:guid}/revoke")]
    public async Task<IActionResult> Revoke(Guid id,RevokeAccess request,CancellationToken ct)
    {
        await using var c=await db.Open(ct);await using var t=await c.BeginTransactionAsync(ct);await Setup(c,t,"facilities.assign_accounts",request.Reason,ct);
        ApiException.Require(await WorkforceDb.Execute(c,t,"UPDATE app.employee_object_access SET is_active=false WHERE id=@id::uuid AND is_active",ct,("id",id))==1,"Назначение не найдено либо уже отозвано.",404);
        await t.CommitAsync(ct);return NoContent();
    }
    [HttpGet("audit")]
    public async Task<JsonArray> Audit(CancellationToken ct)
    {
        await using var c=await db.Open(ct);await WorkforceDb.Permission(c,null,Actor,"audit.read",ct);
        return await WorkforceDb.Rows(c,null,"SELECT jsonb_build_object('id',id,'time',occurred_at,'actorId',actor_id,'action',action_key,'resourceId',resource_id)::text FROM app.audit_log ORDER BY id DESC LIMIT 200",ct);
    }
}
