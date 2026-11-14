using System.Net;
using System.Security.Claims;
using BackendFork.Auth;
using BackendFork.Contracts;
using BackendFork.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;

namespace BackendFork.Controllers;

public sealed record AuthStatus(bool Configured);
public sealed record EngineerResponse(string Id, string DisplayName);

[ApiController, Route("api/auth")]
public sealed class AuthController(PinSessionService sessions) : ControllerBase
{
    [HttpGet("status"), AllowAnonymous]
    public async Task<ActionResult<AuthStatus>> Status(CancellationToken ct) => new AuthStatus(await sessions.IsConfiguredAsync(ct));

    [HttpPost("setup"), AllowAnonymous, EnableRateLimiting("pin")]
    public async Task<ActionResult<SessionResponse>> Setup(SetupRequest request, CancellationToken ct)
    {
        var remote = HttpContext.Connection.RemoteIpAddress;
        ApiException.Require(remote is not null && IPAddress.IsLoopback(remote.IsIPv4MappedToIPv6 ? remote.MapToIPv4() : remote),
            "Первичная настройка PIN доступна только на компьютере с бэком через localhost.", 403);
        return await sessions.SetupAsync(request, ct);
    }
    [HttpPost("login"), AllowAnonymous, EnableRateLimiting("pin")]
    public async Task<ActionResult<SessionResponse>> Login(LoginRequest request, CancellationToken ct) =>
        await sessions.LoginAsync(request.Pin, ct);

    [HttpGet("me"), Authorize]
    public ActionResult<EngineerResponse> Me() =>
        new EngineerResponse(User.FindFirstValue(ClaimTypes.NameIdentifier)!, User.Identity!.Name!);

    [HttpPost("logout"), Authorize, ProducesResponseType(204)]
    public IActionResult Logout()
    {
        sessions.Revoke(Request.Headers.Authorization.ToString()[7..].Trim());
        return NoContent();
    }
}
