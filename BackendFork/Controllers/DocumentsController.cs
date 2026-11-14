using System.Security.Claims;
using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Services;
using BackendFork.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

[ApiController,Authorize,Route("api/documents")]
public sealed class DocumentsController(DocumentService documents):ControllerBase
{
    [HttpGet]public async Task<ActionResult<List<DocumentResponse>>> List([FromQuery]Guid? equipmentId,CancellationToken ct)=>await documents.ListAsync(equipmentId,ct);
    [HttpPost,Consumes("multipart/form-data"),RequestSizeLimit(12*1024*1024),ProducesResponseType<DocumentResponse>(201)]
    public async Task<ActionResult<DocumentResponse>> Upload([FromForm]IFormFile file,[FromForm]DocumentCategory category,[FromForm]Guid? equipmentId,CancellationToken ct)
    {var result=await documents.UploadAsync(file,equipmentId,category,User.FindFirstValue(ClaimTypes.NameIdentifier)!,ct);return Created(result.Url,result);}
    [HttpGet("{id:guid}")]
    public async Task<IActionResult> Download(Guid id,CancellationToken ct)
    {
        var document=await documents.GetAsync(id,ct);ApiException.Require(System.IO.File.Exists(documents.PathFor(document)),"Файл отсутствует.",404);
        return PhysicalFile(documents.PathFor(document),document.ContentType,document.OriginalName);
    }
}
