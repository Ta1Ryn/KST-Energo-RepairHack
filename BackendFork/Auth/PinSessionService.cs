using System.Collections.Concurrent;
using System.Security.Cryptography;
using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Infrastructure;
using Microsoft.Extensions.Options;

namespace BackendFork.Auth;

public sealed record PinSession(string EngineerId, string DisplayName, DateTimeOffset ExpiresAt);
public sealed class PinSessionService(IEquipmentRepository repository, IOptions<BackendOptions> options, TimeProvider clock)
{
    private const int Iterations = 210000;
    private readonly ConcurrentDictionary<string, PinSession> sessions = new();

    public async Task<bool> IsConfiguredAsync(CancellationToken ct) =>
        await repository.ReadAsync(s => s.Account is not null, ct);

    public async Task<SessionResponse> SetupAsync(SetupRequest request, CancellationToken ct)
    {
        var salt = RandomNumberGenerator.GetBytes(32);
        var account = new PinAccount
        {
            DisplayName = request.DisplayName.Trim(), Salt = Convert.ToBase64String(salt),
            Hash = Convert.ToBase64String(HashPin(request.Pin, salt))
        };
        await repository.WriteAsync(s =>
        {
            ApiException.Require(s.Account is null, "PIN уже настроен.", 409);
            s.Account = account;
            return true;
        }, ct);
        return CreateSession(account);
    }
    public async Task<SessionResponse> LoginAsync(string pin, CancellationToken ct)
    {
        var account = await repository.ReadAsync(s => s.Account, ct)
            ?? throw new ApiException(409, "Сначала настройте PIN на компьютере с бэком.");
        ApiException.Require(CryptographicOperations.FixedTimeEquals(
            HashPin(pin, Convert.FromBase64String(account.Salt)), Convert.FromBase64String(account.Hash)),
            "Неверный PIN.", 401);
        return CreateSession(account);
    }
    private static byte[] HashPin(string pin, byte[] salt) =>
        Rfc2898DeriveBytes.Pbkdf2(pin, salt, Iterations, HashAlgorithmName.SHA256, 32);
    private static string TokenKey(string token) =>
        Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(token)));
    private SessionResponse CreateSession(PinAccount account)
    {
        foreach (var pair in sessions)
            if (pair.Value.ExpiresAt <= clock.GetUtcNow()) sessions.TryRemove(pair.Key, out _);
        var token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
        var expiry = clock.GetUtcNow().AddHours(Math.Clamp(options.Value.SessionHours, 1, 72));
        sessions[TokenKey(token)] = new(account.Id, account.DisplayName, expiry);
        return new(token, expiry, account.Id, account.DisplayName);
    }
    public PinSession? Find(string token)
    {
        var key = TokenKey(token);
        if (!sessions.TryGetValue(key, out var session)) return null;
        if (session.ExpiresAt > clock.GetUtcNow()) return session;
        sessions.TryRemove(key, out _);
        return null;
    }
    public void Revoke(string token) => sessions.TryRemove(TokenKey(token), out _);
}
