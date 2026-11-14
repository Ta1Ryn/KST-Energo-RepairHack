using System.Net;
using System.Text.Json.Nodes;
using BackendFork.Infrastructure;
using BackendFork.Workforce;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;

namespace BackendFork.Controllers;

[ApiController,Route("api/workforce")]
public sealed class WorkforceController(WorkforceSessions sessions):ControllerBase
{
    [HttpGet("status"),AllowAnonymous] public Task<JsonObject> Status(CancellationToken ct)=>sessions.Status(ct);
    [HttpPost("bootstrap"),AllowAnonymous,EnableRateLimiting("pin")]
    public Task<JsonObject> Bootstrap(BootstrapRequest request,CancellationToken ct)
    {
        var remote=HttpContext.Connection.RemoteIpAddress;
        ApiException.Require(remote is not null && IPAddress.IsLoopback(remote.IsIPv4MappedToIPv6?remote.MapToIPv4():remote),"Создайте первого администратора на ПК через localhost.",403);
        return sessions.Bootstrap(request,ct);
    }
    [HttpPost("login"),AllowAnonymous,EnableRateLimiting("pin")] public Task<JsonObject> Login(WorkforceLogin request,CancellationToken ct)=>sessions.Login(request,ct);
    [HttpGet("me"),Authorize] public Task<JsonObject> Me(CancellationToken ct)=>sessions.Me(WorkforceDb.Actor(User),ct);
    [HttpPost("pin"),Authorize,EnableRateLimiting("pin")] public Task<JsonObject> Pin(ChangePinRequest request,CancellationToken ct)=>sessions.Change(WorkforceDb.Actor(User),request,ct);
    [HttpPost("logout"),Authorize] public async Task<IActionResult> Logout(CancellationToken ct)
    { await sessions.Revoke(Request.Headers.Authorization.ToString()[7..].Trim(),ct); return NoContent(); }
}
