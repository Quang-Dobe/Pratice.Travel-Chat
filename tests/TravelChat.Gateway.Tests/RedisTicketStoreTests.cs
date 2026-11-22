using System.Globalization;
using System.Security.Claims;
using System.Text;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Caching.Distributed;
using Microsoft.Extensions.Options;
using Shouldly;
using TravelChat.Gateway.Features.Session;

namespace TravelChat.Gateway.Tests;

public sealed class RedisTicketStoreTests
{
    private const string ClaimValue = "traveller-4f2a9c-unique";

    private static readonly DateTimeOffset s_start = new(2026, 1, 1, 9, 0, 0, TimeSpan.Zero);
    private static readonly TimeSpan s_idle = TimeSpan.FromMinutes(30);
    private static readonly TimeSpan s_limit = TimeSpan.FromHours(8);

    private readonly RecordingCache _cache = new();
    private readonly MutableTimeProvider _clock = new(s_start);
    private readonly RedisTicketStore _store;

    public RedisTicketStoreTests()
    {
        var options = Options.Create(new GatewaySessionOptions { IdleTimeout = s_idle, AbsoluteLimit = s_limit });
        _store = new RedisTicketStore(_cache, new EphemeralDataProtectionProvider(), _clock, options);
    }

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private AuthenticationTicket NewTicket(TimeSpan? lifetime = null)
    {
        var identity = new ClaimsIdentity([new Claim(ClaimTypes.NameIdentifier, ClaimValue)], "test");
        var properties = new AuthenticationProperties { ExpiresUtc = _clock.GetUtcNow() + (lifetime ?? s_idle) };
        return new AuthenticationTicket(new ClaimsPrincipal(identity), properties, "Cookies");
    }

    private static DateTimeOffset SignedInAt(AuthenticationTicket ticket) =>
        DateTimeOffset.Parse(ticket.Properties.Items[RedisTicketStore.SignedInAtKey]!, CultureInfo.InvariantCulture);

    [Fact]
    public async Task StoreThenRetrieveRoundTripsTheTicket()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);

        var retrieved = await _store.RetrieveAsync(key, Ct);

        retrieved.ShouldNotBeNull();
        retrieved.Principal.FindFirstValue(ClaimTypes.NameIdentifier).ShouldBe(ClaimValue);
    }

    [Fact]
    public async Task StoreStampsTheSignInTime()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);

        var retrieved = await _store.RetrieveAsync(key, Ct);

        retrieved.ShouldNotBeNull();
        SignedInAt(retrieved).ShouldBe(s_start);
    }

    [Fact]
    public async Task RenewKeepsTheOriginalSignInStamp()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);
        _clock.Advance(TimeSpan.FromMinutes(10));
        var retrieved = await _store.RetrieveAsync(key, Ct);
        retrieved.ShouldNotBeNull();

        await _store.RenewAsync(key, retrieved, Ct);

        var renewed = await _store.RetrieveAsync(key, Ct);
        renewed.ShouldNotBeNull();
        SignedInAt(renewed).ShouldBe(s_start);
    }

    [Fact]
    public async Task RenewWithAnUnstampedTicketIsAFreshSignInStampedNow()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);
        _clock.Advance(TimeSpan.FromHours(1));

        await _store.RenewAsync(key, NewTicket(TimeSpan.FromHours(9)), Ct);

        var renewed = await _store.RetrieveAsync(key, Ct);
        renewed.ShouldNotBeNull();
        SignedInAt(renewed).ShouldBe(_clock.GetUtcNow());
        renewed.Properties.ExpiresUtc.ShouldBe(_clock.GetUtcNow() + s_limit);
        _cache.Entries[key].Options.AbsoluteExpirationRelativeToNow.ShouldBe(s_limit);
    }

    [Fact]
    public async Task RenewAfterRemoveDoesNotRecreateTheEntry()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);
        var ticket = await _store.RetrieveAsync(key, Ct);
        ticket.ShouldNotBeNull();
        await _store.RemoveAsync(key, Ct);
        var writesBefore = _cache.SetCount;

        await _store.RenewAsync(key, ticket, Ct);

        _cache.Entries.ShouldNotContainKey(key);
        _cache.SetCount.ShouldBe(writesBefore);
    }

    [Fact]
    public async Task CachedBytesDoNotContainClaimValuesAsPlainText()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);

        var bytes = _cache.Entries[key].Value;

        bytes.AsSpan().IndexOf(Encoding.UTF8.GetBytes(ClaimValue)).ShouldBe(-1);
    }

    [Fact]
    public async Task StoreCapsTicketExpiryAtSignInPlusLimit()
    {
        var key = await _store.StoreAsync(NewTicket(TimeSpan.FromHours(9)), Ct);

        var retrieved = await _store.RetrieveAsync(key, Ct);

        retrieved.ShouldNotBeNull();
        retrieved.Properties.ExpiresUtc.ShouldBe(s_start + s_limit);
    }

    [Fact]
    public async Task StoreCapsCacheEntryExpiryAtSignInPlusLimit()
    {
        var key = await _store.StoreAsync(NewTicket(TimeSpan.FromHours(9)), Ct);

        _cache.Entries[key].Options.AbsoluteExpirationRelativeToNow.ShouldBe(s_limit);
    }

    [Fact]
    public async Task RenewNearTheLimitCapsTicketExpiryAtSignInPlusLimit()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);
        _clock.Advance(TimeSpan.FromHours(7) + TimeSpan.FromMinutes(45));
        var ticket = await _store.RetrieveAsync(key, Ct);
        ticket.ShouldNotBeNull();
        ticket.Properties.ExpiresUtc = _clock.GetUtcNow() + s_idle;

        await _store.RenewAsync(key, ticket, Ct);

        var renewed = await _store.RetrieveAsync(key, Ct);
        renewed.ShouldNotBeNull();
        renewed.Properties.ExpiresUtc.ShouldBe(s_start + s_limit);
    }

    [Fact]
    public async Task RenewNearTheLimitCapsCacheEntryExpiryAtSignInPlusLimit()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);
        _clock.Advance(TimeSpan.FromHours(7) + TimeSpan.FromMinutes(45));
        var ticket = await _store.RetrieveAsync(key, Ct);
        ticket.ShouldNotBeNull();
        ticket.Properties.ExpiresUtc = _clock.GetUtcNow() + s_idle;

        await _store.RenewAsync(key, ticket, Ct);

        _cache.Entries[key].Options.AbsoluteExpirationRelativeToNow.ShouldBe(TimeSpan.FromMinutes(15));
    }

    [Fact]
    public async Task RetrieveAtTheLimitReturnsNull()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);
        _clock.Advance(s_limit);

        (await _store.RetrieveAsync(key, Ct)).ShouldBeNull();
    }

    [Fact]
    public async Task RenewAtTheLimitRemovesTheEntryAndWritesNothing()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);
        var ticket = await _store.RetrieveAsync(key, Ct);
        ticket.ShouldNotBeNull();
        var writesBefore = _cache.SetCount;
        _clock.Advance(s_limit);

        await _store.RenewAsync(key, ticket, Ct);

        _cache.Entries.ShouldNotContainKey(key);
        _cache.SetCount.ShouldBe(writesBefore);
    }

    [Fact]
    public async Task RemoveDeletesTheEntry()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);

        await _store.RemoveAsync(key, Ct);

        _cache.Entries.ShouldNotContainKey(key);
    }

    [Fact]
    public async Task RetrieveAfterRemoveReturnsNull()
    {
        var key = await _store.StoreAsync(NewTicket(), Ct);
        await _store.RemoveAsync(key, Ct);

        (await _store.RetrieveAsync(key, Ct)).ShouldBeNull();
    }

    [Theory]
    [InlineData("POST", "/api/x", true)]
    [InlineData("GET", "/api/users/me", true)]
    [InlineData("GET", "/api/session", false)]
    [InlineData("GET", "/api/session/", false)]
    [InlineData("GET", "/API/SESSION", false)]
    [InlineData("POST", "/api/session", true)]
    [InlineData("GET", "/api/sessionX", true)]
    [InlineData("GET", "/apix", false)]
    [InlineData("GET", "/auth/login", false)]
    public void ShouldRenewOnlyForApiRequestsOtherThanTimeLeft(string method, string path, bool expected)
    {
        var context = new DefaultHttpContext();
        context.Request.Method = method;
        context.Request.Path = path;

        SessionSetup.ShouldRenew(context.Request).ShouldBe(expected);
    }

    private sealed class MutableTimeProvider(DateTimeOffset now) : TimeProvider
    {
        private DateTimeOffset _now = now;

        public override DateTimeOffset GetUtcNow() => _now;

        public void Advance(TimeSpan by) => _now += by;
    }

    private sealed class RecordingCache : IDistributedCache
    {
        public Dictionary<string, (byte[] Value, DistributedCacheEntryOptions Options)> Entries { get; } = [];

        public int SetCount { get; private set; }

        public byte[]? Get(string key) => Entries.TryGetValue(key, out var entry) ? entry.Value : null;

        public Task<byte[]?> GetAsync(string key, CancellationToken token = default) => Task.FromResult(Get(key));

        public void Set(string key, byte[] value, DistributedCacheEntryOptions options)
        {
            SetCount++;
            Entries[key] = (value, options);
        }

        public Task SetAsync(string key, byte[] value, DistributedCacheEntryOptions options, CancellationToken token = default)
        {
            Set(key, value, options);
            return Task.CompletedTask;
        }

        public void Refresh(string key)
        {
        }

        public Task RefreshAsync(string key, CancellationToken token = default) => Task.CompletedTask;

        public void Remove(string key) => Entries.Remove(key);

        public Task RemoveAsync(string key, CancellationToken token = default)
        {
            Remove(key);
            return Task.CompletedTask;
        }
    }
}
