using BackendFork.Contracts;
using BackendFork.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

[ApiController, Authorize, Route("api/equipment")]
public sealed class EquipmentController(EquipmentService service) : ControllerBase
{
    [HttpGet]
    public async Task<ActionResult<Page<EquipmentSummary>>> List([FromQuery] string? search, [FromQuery] int offset = 0,
        [FromQuery] int limit = 30, CancellationToken ct = default) => await service.ListAsync(search, offset, limit, ct);
    [HttpGet("{equipmentId:guid}")]
    public async Task<ActionResult<EquipmentCard>> Get(Guid equipmentId, CancellationToken ct) => await service.GetAsync(equipmentId, ct);
    [HttpPost, ProducesResponseType<EquipmentCard>(201)]
    public async Task<ActionResult<EquipmentCard>> Create(EquipmentRequest request, CancellationToken ct)
    {
        var result = await service.CreateAsync(request, ct);
        return CreatedAtAction(nameof(Get), new { equipmentId = result.Id }, result);
    }
    [HttpGet("{equipmentId:guid}/components/{componentId:guid}")]
    public async Task<ActionResult<ComponentCard>> Component(Guid equipmentId, Guid componentId, CancellationToken ct) =>
        await service.GetComponentAsync(equipmentId, componentId, ct);
    [HttpPost("{equipmentId:guid}/components"), ProducesResponseType<ComponentCard>(201)]
    public async Task<ActionResult<ComponentCard>> AddComponent(Guid equipmentId, ComponentRequest request, CancellationToken ct)
    {
        var result = await service.AddComponentAsync(equipmentId, request, ct);
        return CreatedAtAction(nameof(Component), new { equipmentId, componentId = result.Id }, result);
    }
    [HttpPut("{equipmentId:guid}/components/{componentId:guid}/definition")]
    public async Task<ActionResult<ComponentCard>> Definition(Guid equipmentId, Guid componentId, ComponentDefinitionRequest request, CancellationToken ct) =>
        await service.UpdateDefinitionAsync(equipmentId, componentId, request, ct);
    [HttpPost("{equipmentId:guid}/components/{componentId:guid}/telemetry")]
    public async Task<ActionResult<ComponentCard>> Telemetry(Guid equipmentId, Guid componentId, TelemetryRequest request, CancellationToken ct) =>
        await service.TelemetryAsync(equipmentId, componentId, request, ct);
}
