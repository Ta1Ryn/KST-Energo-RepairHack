using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Infrastructure;
using BackendFork.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

public sealed record AssistantRequest(string Question,Guid? EquipmentId);
[ApiController,Authorize,Route("api/operations")]
public sealed class OperationsController(IEquipmentRepository repository,EquipmentService equipment, BackendFork.Workforce.WorkforceDb db):ControllerBase
{
    [HttpGet("findings")]
    public async Task<System.Text.Json.Nodes.JsonArray> Findings(CancellationToken ct)
    {
        var actor=BackendFork.Workforce.WorkforceDb.Actor(User);
        await using var c=await db.Open(ct);
        return await BackendFork.Workforce.WorkforceDb.Rows(c,null,"SELECT jsonb_build_object('id',f.id,'inspectionId',f.inspection_id,'equipmentId',i.equipment_id,'equipmentName',e.name,'componentId',f.component_id,'type',f.defect_type_code,'title',f.title,'description',f.description,'status',f.status,'source',f.source,'detectedAt',f.detected_at)::text FROM reports.inspection_findings f JOIN reports.inspections i ON i.id=f.inspection_id JOIN catalog.equipment e ON e.id=i.equipment_id WHERE app.account_can_read_equipment_v7(@actor,e.id) AND (app.account_has_permission_v4(@actor,'findings.read_all') OR (f.detected_by_account_id=@actor AND app.account_has_permission_v4(@actor,'findings.read_own'))) ORDER BY f.detected_at DESC LIMIT 500",ct,("actor",actor));
    }
    [HttpGet("reports")]
    public Task<Page<Inspection>> Reports([FromQuery]int offset=0,[FromQuery]int limit=100,CancellationToken ct=default)
    {
        EquipmentService.PageBounds(offset,limit);
        return repository.ReadAsync(s=>{
            var all=s.Inspections.OrderByDescending(x=>x.PerformedAt).ToArray();
            return new Page<Inspection>(all.Skip(offset).Take(limit).ToArray(),all.Length,offset,limit);
        },ct);
    }
    [HttpPost("assistant")]
    public async Task<object> Assistant(AssistantRequest request,CancellationToken ct)
    {
        ApiException.Require(!string.IsNullOrWhiteSpace(request.Question) && request.Question.Length<=2000,"Введите вопрос до 2000 символов.");
        var page=await equipment.ListAsync(null,0,1,ct);
        var id=request.EquipmentId??page.Items.FirstOrDefault()?.Id;
        if(id is null)return new {mode="rules",answer="Нет доступного оборудования. Администратор должен назначить доступ к насосу."};
        var card=await equipment.GetAsync(id.Value,ct);
        var lines=new List<string>{card.Name+" · "+card.Model,"Справочные рекомендации по текущим датчикам и нормам. Анализ фотографий выполняется отдельно в осмотре."};
        foreach(var component in card.Components)
        {
            lines.Add(component.Name+": "+(component.RequiresAttention?"требует проверки":"по доступным данным"));
            lines.AddRange(component.Warnings);
            lines.AddRange(component.Recommendations?.Select(x=>x.Message)??[]);
            lines.AddRange(component.Resource.Limitations);
        }
        return new {mode="rules",equipmentId=id,answer=string.Join("\n",lines.Distinct())};
    }
}
