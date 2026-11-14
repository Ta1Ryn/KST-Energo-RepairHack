using BackendFork.Workforce;
using System.Security.Claims;
using System.Text.Encodings.Web;
using Microsoft.AspNetCore.Authentication;
using Microsoft.Extensions.Options;

namespace BackendFork.Auth;

public sealed class PinAuthenticationHandler(
    IOptionsMonitor<AuthenticationSchemeOptions> options, ILoggerFactory logger,
    UrlEncoder encoder, PinSessionService sessions, WorkforceSessions workforce) : AuthenticationHandler<AuthenticationSchemeOptions>(options, logger, encoder)
{
    protected override async Task<AuthenticateResult> HandleAuthenticateAsync()
    {
        var header = Request.Headers.Authorization.ToString();
        if (!header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return AuthenticateResult.NoResult();
        var token = header[7..].Trim();
        var remote = workforce.Enabled ? await workforce.Find(token,Context.RequestAborted) : null;
        var session = workforce.Enabled ? (remote is null ? null : new PinSession(remote.AccountId,remote.DisplayName,DateTimeOffset.MaxValue)) : (token.Length == 64 ? sessions.Find(token) : null);
        if (session is null) return AuthenticateResult.Fail("Сессия истекла или недействительна.");
        var identity = new ClaimsIdentity([
            new Claim(ClaimTypes.NameIdentifier, session.EngineerId),
            new Claim(ClaimTypes.Name, session.DisplayName)], Scheme.Name);
        return AuthenticateResult.Success(new AuthenticationTicket(new ClaimsPrincipal(identity), Scheme.Name));
    }
}
