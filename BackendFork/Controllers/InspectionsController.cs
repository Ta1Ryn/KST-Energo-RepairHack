using System.Security.Claims;
using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

[ApiController, Authorize, Route("api/equipment/{equipmentId:guid}/inspections")]
public sealed class InspectionsController(EquipmentService service) : ControllerBase
{
    [HttpGet]
    public async Task<ActionResult<Page<Inspection>>> History(Guid equipmentId, [FromQuery] int offset = 0,
        [FromQuery] int limit = 30, CancellationToken ct = default) => await service.HistoryAsync(equipmentId, offset, limit, ct);
    [HttpPost, ProducesResponseType<Inspection>(201)]
    public async Task<ActionResult<Inspection>> Save(Guid equipmentId, InspectionRequest request, CancellationToken ct)
    {
        var result = await service.InspectAsync(equipmentId, request, User.FindFirstValue(ClaimTypes.NameIdentifier)!,
            User.Identity!.Name!, ct);
        return StatusCode(201, result);
    }
}
