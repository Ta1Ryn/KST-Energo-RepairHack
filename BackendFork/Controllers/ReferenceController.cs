using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

[ApiController,Authorize,Route("api")]
public sealed class ReferenceController(ReferenceService service,EquipmentService equipment):ControllerBase
{
    [HttpGet("defect-types")] public ActionResult<DefectType[]> Defects()=>DefectCatalog.All;
    [HttpGet("standards/sensor-norms")] public async Task<ActionResult<List<SensorNorm>>> Norms(CancellationToken ct)=>await service.NormsAsync(ct);
    [HttpPost("standards/sensor-norms")] public async Task<ActionResult<SensorNorm>> CreateNorm(SensorNormRequest request,CancellationToken ct)=>await service.SaveNormAsync(null,request,ct);
    [HttpPut("standards/sensor-norms/{id:guid}")] public async Task<ActionResult<SensorNorm>> UpdateNorm(Guid id,SensorNormRequest request,CancellationToken ct)=>await service.SaveNormAsync(id,request,ct);
    [HttpGet("instructions")] public async Task<ActionResult<List<Instruction>>> Instructions(CancellationToken ct)=>await service.InstructionsAsync(ct);
    [HttpPost("instructions")] public async Task<ActionResult<Instruction>> CreateInstruction(InstructionRequest request,CancellationToken ct)=>await service.SaveInstructionAsync(null,request,ct);
    [HttpPut("instructions/{id:guid}")] public async Task<ActionResult<Instruction>> UpdateInstruction(Guid id,InstructionRequest request,CancellationToken ct)=>await service.SaveInstructionAsync(id,request,ct);
    [HttpGet("recommendation-rules")] public async Task<ActionResult<List<RecommendationRule>>> Rules(CancellationToken ct)=>await service.RulesAsync(ct);
    [HttpPost("recommendation-rules")] public async Task<ActionResult<RecommendationRule>> CreateRule(RecommendationRuleRequest request,CancellationToken ct)=>await service.SaveRuleAsync(null,request,ct);
    [HttpPut("recommendation-rules/{id:guid}")] public async Task<ActionResult<RecommendationRule>> UpdateRule(Guid id,RecommendationRuleRequest request,CancellationToken ct)=>await service.SaveRuleAsync(id,request,ct);
    [HttpPut("equipment/{equipmentId:guid}/passport")]
    public async Task<ActionResult<EquipmentCard>> Passport(Guid equipmentId,PassportRequest request,CancellationToken ct)
    {await service.PassportAsync(equipmentId,request,ct);return await equipment.GetAsync(equipmentId,ct);}
}
