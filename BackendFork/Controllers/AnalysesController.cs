using System.Security.Claims;
using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Infrastructure;
using BackendFork.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

[ApiController, Authorize, Route("api/equipment/{equipmentId:guid}/components/{componentId:guid}/analyses")]
public sealed class AnalysesController(LocalAiClient ai, IEquipmentRepository repository) : ControllerBase
{
    [HttpPost("/api/vision/live"), RequestSizeLimit(600 * 1024), RequestFormLimits(MultipartBodyLengthLimit = 550 * 1024)]
    public async Task<ActionResult<AiResult>> Live([FromForm] IFormFile file, CancellationToken ct)
    {
        Response.Headers.CacheControl = "no-store";
        return await ai.PreviewAsync(file, ct);
    }

    [HttpPost, ProducesResponseType<Analysis>(201)]
    public async Task<ActionResult<Analysis>> Analyze(Guid equipmentId, Guid componentId, AnalyzeRequest request, CancellationToken ct)
    {
        var result = await ai.AnalyzeAsync(equipmentId, componentId, request, User.FindFirstValue(ClaimTypes.NameIdentifier)!, ct);
        return StatusCode(201, result);
    }
    [HttpGet]
    public async Task<ActionResult<Page<Analysis>>> History(Guid equipmentId, Guid componentId, [FromQuery] int offset = 0,
        [FromQuery] int limit = 30, CancellationToken ct = default)
    {
        EquipmentService.PageBounds(offset, limit);
        return await repository.ReadAsync(s =>
        {
            EquipmentService.FindComponent(EquipmentService.FindEquipment(s, equipmentId), componentId);
            var all = s.Analyses.Where(x => x.EquipmentId == equipmentId && x.ComponentId == componentId)
                .OrderByDescending(x => x.CreatedAt).ToList();
            return new Page<Analysis>(all.Skip(offset).Take(limit).ToList(), all.Count, offset, limit);
        }, ct);
    }
}
