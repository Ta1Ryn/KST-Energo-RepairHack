using System.Security.Claims;
using BackendFork.Contracts;
using BackendFork.Infrastructure;
using BackendFork.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace BackendFork.Controllers;

[ApiController, Authorize, Route("api/photos")]
public sealed class PhotosController(PhotoService photos) : ControllerBase
{
    [HttpPost, Consumes("multipart/form-data"), RequestSizeLimit(12 * 1024 * 1024), ProducesResponseType<PhotoResponse>(201)]
    public async Task<ActionResult<PhotoResponse>> Upload([FromForm] IFormFile file, CancellationToken ct)
    {
        var photo = await photos.UploadAsync(file, User.FindFirstValue(ClaimTypes.NameIdentifier)!, ct);
        return Created(photo.Url, photo);
    }
    [HttpGet("{photoId:guid}"), Produces("image/jpeg", "image/png", "image/webp")]
    public async Task<IActionResult> Download(Guid photoId, CancellationToken ct)
    {
        var photo = await photos.GetAsync(photoId, ct);
        ApiException.Require(System.IO.File.Exists(photos.PathFor(photo)), "Файл фото отсутствует.", 404);
        return PhysicalFile(photos.PathFor(photo), photo.ContentType);
    }
}
