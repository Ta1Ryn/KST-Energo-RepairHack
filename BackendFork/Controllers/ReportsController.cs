using BackendFork.Domain;
using BackendFork.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

[ApiController,Authorize,Route("api/equipment/{equipmentId:guid}/inspections")]
public sealed class ReportsController(PdfReportService reports):ControllerBase
{
    [HttpGet("{inspectionId:guid}")]
    public async Task<ActionResult<Inspection>> Get(Guid equipmentId,Guid inspectionId,CancellationToken ct)=>await reports.GetAsync(equipmentId,inspectionId,ct);
    [HttpGet("{inspectionId:guid}/pdf"),Produces("application/pdf")]
    public async Task<IActionResult> Pdf(Guid equipmentId,Guid inspectionId,CancellationToken ct)=>
        File(await reports.BuildAsync(equipmentId,inspectionId,ct),"application/pdf","report-"+inspectionId+".pdf");
}
