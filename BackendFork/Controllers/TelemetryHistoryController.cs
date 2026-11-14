using System.Text.Json.Nodes;
using BackendFork.Infrastructure;
using BackendFork.Services;
using BackendFork.Workforce;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

[ApiController, Authorize, Route("api/equipment/{equipmentId:guid}/telemetry-history")]
public sealed class TelemetryHistoryController(WorkforceDb db, EquipmentService equipment) : ControllerBase
{
    [HttpGet]
    public async Task<JsonArray> Get(Guid equipmentId, [FromQuery] int points = 120,
        [FromQuery] int hours = 24, CancellationToken ct = default)
    {
        ApiException.Require(points is >= 2 and <= 500 && hours is >= 1 and <= 720,
            "points: 2–500; hours: 1–720.");
        await equipment.GetAsync(equipmentId, ct);
        await using var c = await db.Open(ct);
        // Access is checked inside the history query as well as on the card.
        return await WorkforceDb.Rows(c, null, """
            WITH recent AS (
                SELECT r.component_id,r.sensor_key,r.value,r.unit,r.measured_at,
                    row_number() OVER (PARTITION BY r.component_id,r.sensor_key ORDER BY r.measured_at DESC) AS n
                FROM telemetry.readings r
                JOIN catalog.components cp ON cp.id=r.component_id
                JOIN catalog.installations i ON i.id=r.installation_id AND i.removed_at IS NULL
                WHERE cp.equipment_id=@equipment::uuid
                    AND app.account_can_read_equipment_v7(@actor,cp.equipment_id)
                    AND r.measured_at >= statement_timestamp()-(@hours::integer * interval '1 hour')
            )
            SELECT jsonb_build_object('componentId',component_id,'key',sensor_key,'value',value,
                'unit',unit,'measuredAt',measured_at)::text
            FROM recent WHERE n<=@points::integer ORDER BY measured_at
            """, ct, ("equipment", equipmentId), ("actor", WorkforceDb.Actor(User)),
            ("hours", hours), ("points", points));
    }
}
