using System.Globalization;
using System.Security.Cryptography;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.Extensions.Caching.Distributed;
using Microsoft.Extensions.Options;

namespace TravelChat.Gateway.Features.Session;

public sealed class RedisTicketStore(
    IDistributedCache cache,
    IDataProtectionProvider dataProtection,
    TimeProvider clock,
    IOptions<GatewaySessionOptions> options) : ITicketStore
{
    public const string SignedInAtKey = "gateway.signed_in_at";

    private const string Purpose = "TravelChat.Gateway.Session";

    private readonly IDataProtector _protector = dataProtection.CreateProtector(Purpose);

    public Task<string> StoreAsync(AuthenticationTicket ticket) =>
        StoreAsync(ticket, CancellationToken.None);

    public Task<string> StoreAsync(AuthenticationTicket ticket, CancellationToken cancellationToken)
    {
        var signedInAt = clock.GetUtcNow();
        ticket.Properties.Items[SignedInAtKey] = signedInAt.UtcDateTime.ToString("O", CultureInfo.InvariantCulture);
        return StoreAsync(ticket, signedInAt, cancellationToken);
    }

    public Task<string> StoreAsync(AuthenticationTicket ticket, HttpContext httpContext, CancellationToken cancellationToken) =>
        StoreAsync(ticket, cancellationToken);

    public Task RenewAsync(string key, AuthenticationTicket ticket) =>
        RenewAsync(key, ticket, CancellationToken.None);

    public async Task RenewAsync(string key, AuthenticationTicket ticket, CancellationToken cancellationToken)
    {
        if (await cache.GetAsync(key, cancellationToken) is null)
        {
            return;
        }

        if (!TryReadSignedInAt(ticket, out var signedInAt))
        {
            signedInAt = clock.GetUtcNow();
            ticket.Properties.Items[SignedInAtKey] = signedInAt.UtcDateTime.ToString("O", CultureInfo.InvariantCulture);
        }
        else if (HasEnded(signedInAt))
        {
            await cache.RemoveAsync(key, cancellationToken);
            return;
        }

        await WriteAsync(key, ticket, signedInAt, cancellationToken);
    }

    public Task RenewAsync(string key, AuthenticationTicket ticket, HttpContext httpContext, CancellationToken cancellationToken) =>
        RenewAsync(key, ticket, cancellationToken);

    public Task<AuthenticationTicket?> RetrieveAsync(string key) =>
        RetrieveAsync(key, CancellationToken.None);

    public async Task<AuthenticationTicket?> RetrieveAsync(string key, CancellationToken cancellationToken)
    {
        var payload = await cache.GetAsync(key, cancellationToken);
        if (payload is null)
        {
            return null;
        }

        AuthenticationTicket? ticket;
        try
        {
            ticket = TicketSerializer.Default.Deserialize(_protector.Unprotect(payload));
        }
        catch (CryptographicException)
        {
            return null;
        }

        if (ticket is null || !TryReadSignedInAt(ticket, out var signedInAt) || HasEnded(signedInAt))
        {
            return null;
        }

        return ticket;
    }

    public Task<AuthenticationTicket?> RetrieveAsync(string key, HttpContext httpContext, CancellationToken cancellationToken) =>
        RetrieveAsync(key, cancellationToken);

    public Task RemoveAsync(string key) =>
        RemoveAsync(key, CancellationToken.None);

    public Task RemoveAsync(string key, CancellationToken cancellationToken) =>
        cache.RemoveAsync(key, cancellationToken);

    public Task RemoveAsync(string key, HttpContext httpContext, CancellationToken cancellationToken) =>
        RemoveAsync(key, cancellationToken);

    private async Task<string> StoreAsync(AuthenticationTicket ticket, DateTimeOffset signedInAt, CancellationToken cancellationToken)
    {
        var key = Guid.NewGuid().ToString("N");
        await WriteAsync(key, ticket, signedInAt, cancellationToken);
        return key;
    }

    private async Task WriteAsync(string key, AuthenticationTicket ticket, DateTimeOffset signedInAt, CancellationToken cancellationToken)
    {
        var end = signedInAt + options.Value.AbsoluteLimit;
        if (ticket.Properties.ExpiresUtc is { } requested && requested < end)
        {
            end = requested;
        }

        var remaining = end - clock.GetUtcNow();
        if (remaining <= TimeSpan.Zero)
        {
            await cache.RemoveAsync(key, cancellationToken);
            return;
        }

        ticket.Properties.ExpiresUtc = end;
        var payload = _protector.Protect(TicketSerializer.Default.Serialize(ticket));
        await cache.SetAsync(
            key,
            payload,
            new DistributedCacheEntryOptions { AbsoluteExpirationRelativeToNow = remaining },
            cancellationToken);
    }

    private bool HasEnded(DateTimeOffset signedInAt) =>
        signedInAt + options.Value.AbsoluteLimit <= clock.GetUtcNow();

    private static bool TryReadSignedInAt(AuthenticationTicket ticket, out DateTimeOffset signedInAt)
    {
        signedInAt = default;
        return ticket.Properties.Items.TryGetValue(SignedInAtKey, out var raw)
            && DateTimeOffset.TryParse(
                raw,
                CultureInfo.InvariantCulture,
                DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                out signedInAt);
    }
}
