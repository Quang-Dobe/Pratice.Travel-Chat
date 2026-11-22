using System.Collections.Concurrent;
using System.Net;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Caching.Distributed;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Shouldly;

namespace TravelChat.Gateway.Tests;

public sealed class SessionTimeoutTests : IDisposable
{
    private const string MessagesPath = "/api/conversations/abc/messages";
    private const string MePath = "/api/users/me";
    private const string TimeLeftPath = "/api/session";
    private const string LogoutPath = "/auth/logout";
    private const string IdleEnd = "idle";

    private static readonly DateTimeOffset s_start = new(2026, 1, 1, 9, 0, 0, TimeSpan.Zero);
    private static readonly TimeSpan s_idle = TimeSpan.FromMinutes(30);

    private readonly TestClock _clock = new(s_start);
    private readonly RecordingCache _cache = new();
    private readonly GatewayFactory _gateway = new();
    private readonly WebApplicationFactory<Program> _app;
    private readonly HttpClient _client;

    public SessionTimeoutTests()
    {
        _app = _gateway.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
        {
            services.Replace(ServiceDescriptor.Singleton<TimeProvider>(_clock));
            services.Replace(ServiceDescriptor.Singleton<IDistributedCache>(_cache));
        }));
        _client = _app.CreateClient(
            new WebApplicationFactoryClientOptions { AllowAutoRedirect = false, HandleCookies = false });
    }

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    public void Dispose()
    {
        _client.Dispose();
        _app.Dispose();
        _gateway.Dispose();
    }

    private Task<string> SignInAsync() =>
        GatewayFactory.SignInCookieAsync(_client, "oid-1", Guid.NewGuid().ToString("D"));

    private async Task<HttpStatusCode> StatusAsync(string cookie, HttpMethod method, string path)
    {
        using var response = await CallAsync(cookie, method, path);
        return response.StatusCode;
    }

    private async Task<HttpResponseMessage> CallAsync(string cookie, HttpMethod method, string path)
    {
        using var request = new HttpRequestMessage(method, new Uri(path, UriKind.Relative));
        request.Headers.Add("Cookie", cookie);
        return await _client.SendAsync(request, Ct);
    }

    private Task<HttpStatusCode> SendMessageAsync(string cookie) =>
        StatusAsync(cookie, HttpMethod.Post, MessagesPath);

    private async Task<(int SecondsLeft, string EndsBy)> TimeLeftAsync(string cookie)
    {
        using var response = await CallAsync(cookie, HttpMethod.Get, TimeLeftPath);
        var body = await response.Content.ReadAsStringAsync(Ct);
        response.StatusCode.ShouldBe(HttpStatusCode.OK, body);
        using var document = JsonDocument.Parse(body);
        return (
            document.RootElement.GetProperty("secondsLeft").GetInt32(),
            document.RootElement.GetProperty("endsBy").GetString() ?? string.Empty);
    }

    [Fact]
    public async Task E2E1RegularUseKeepsTheUserSignedIn()
    {
        var cookie = await SignInAsync();

        for (var elapsed = 25; elapsed <= 120; elapsed += 25)
        {
            _clock.Advance(TimeSpan.FromMinutes(25));

            (await SendMessageAsync(cookie)).ShouldBe(HttpStatusCode.OK, $"message at +{elapsed} min");
            var left = await TimeLeftAsync(cookie);
            left.EndsBy.ShouldBe(IdleEnd);
            left.SecondsLeft.ShouldBeInRange(1790, 1800);
        }

        _clock.Advance(TimeSpan.FromMinutes(20));
        var atTwoHours = await TimeLeftAsync(cookie);
        atTwoHours.EndsBy.ShouldBe(IdleEnd);
        atTwoHours.SecondsLeft.ShouldBe(600);

        _gateway.Destinations.Requests.Count.ShouldBe(4);
    }

    [Fact]
    public async Task E2E3UsersMeResetsTimeLeftToTheFullIdleWindow()
    {
        var cookie = await SignInAsync();
        _clock.Advance(TimeSpan.FromMinutes(20));
        (await TimeLeftAsync(cookie)).SecondsLeft.ShouldBe(600);

        (await StatusAsync(cookie, HttpMethod.Get, MePath)).ShouldBe(HttpStatusCode.OK);

        var after = await TimeLeftAsync(cookie);
        after.SecondsLeft.ShouldBe(1800);
        after.EndsBy.ShouldBe(IdleEnd);
    }

    [Fact]
    public async Task E2E8AbsoluteLimitEndsAnActiveSessionAtEightHours()
    {
        var cookie = await SignInAsync();

        for (var sent = 1; sent <= 47; sent++)
        {
            _clock.Advance(TimeSpan.FromMinutes(10));
            (await SendMessageAsync(cookie)).ShouldBe(HttpStatusCode.OK, $"message {sent}");
        }

        var beforeLimit = await TimeLeftAsync(cookie);
        beforeLimit.EndsBy.ShouldBe("limit");
        beforeLimit.SecondsLeft.ShouldBe(600);

        _clock.Advance(TimeSpan.FromMinutes(10));

        (await SendMessageAsync(cookie)).ShouldBe(HttpStatusCode.Unauthorized);
        _gateway.Destinations.Requests.Count.ShouldBe(47);
    }

    [Fact]
    public async Task E2E10CheckingTimeLeftDoesNotKeepTheSessionAlive()
    {
        var cookie = await SignInAsync();

        for (var minutes = 5; minutes <= 25; minutes += 5)
        {
            _clock.Advance(TimeSpan.FromMinutes(5));
            (await TimeLeftAsync(cookie)).SecondsLeft.ShouldBe((int)(s_idle - TimeSpan.FromMinutes(minutes)).TotalSeconds);
        }

        _clock.Advance(TimeSpan.FromMinutes(4) + TimeSpan.FromSeconds(59));
        (await TimeLeftAsync(cookie)).SecondsLeft.ShouldBe(1);

        _clock.Advance(TimeSpan.FromSeconds(2));
        (await StatusAsync(cookie, HttpMethod.Get, TimeLeftPath)).ShouldBe(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task E2E11AnEndedSessionIsNeverRevived()
    {
        var cookie = await SignInAsync();
        _clock.Advance(s_idle + TimeSpan.FromMinutes(1));

        (await StatusAsync(cookie, HttpMethod.Get, MePath)).ShouldBe(HttpStatusCode.Unauthorized);
        _clock.Advance(TimeSpan.FromMinutes(1));
        (await SendMessageAsync(cookie)).ShouldBe(HttpStatusCode.Unauthorized);
        (await StatusAsync(cookie, HttpMethod.Get, TimeLeftPath)).ShouldBe(HttpStatusCode.Unauthorized);
        (await StatusAsync(cookie, HttpMethod.Get, MePath)).ShouldBe(HttpStatusCode.Unauthorized);

        _gateway.Destinations.Requests.ShouldBeEmpty();
        _cache.Keys.ShouldBeEmpty();
    }

    [Fact]
    public async Task E2E12SigningOutEndsTheSessionForGood()
    {
        var cookie = await SignInAsync();
        (await StatusAsync(cookie, HttpMethod.Get, MePath)).ShouldBe(HttpStatusCode.OK);
        _cache.Keys.Count.ShouldBe(1);

        (await StatusAsync(cookie, HttpMethod.Post, LogoutPath)).ShouldBe(HttpStatusCode.NoContent);

        _cache.Keys.ShouldBeEmpty();
        (await StatusAsync(cookie, HttpMethod.Get, MePath)).ShouldBe(HttpStatusCode.Unauthorized);
        (await SendMessageAsync(cookie)).ShouldBe(HttpStatusCode.Unauthorized);
        _gateway.Destinations.Requests.Count.ShouldBe(1);
    }

    private sealed class TestClock(DateTimeOffset now) : TimeProvider
    {
        private DateTimeOffset _now = now;

        public override DateTimeOffset GetUtcNow() => _now;

        public void Advance(TimeSpan by) => _now += by;
    }

    private sealed class RecordingCache : IDistributedCache
    {
        private readonly ConcurrentDictionary<string, byte[]> _entries = new();

        public IReadOnlyCollection<string> Keys => [.. _entries.Keys];

        public byte[]? Get(string key) => _entries.TryGetValue(key, out var value) ? value : null;

        public Task<byte[]?> GetAsync(string key, CancellationToken token = default) => Task.FromResult(Get(key));

        public void Set(string key, byte[] value, DistributedCacheEntryOptions options) => _entries[key] = value;

        public Task SetAsync(string key, byte[] value, DistributedCacheEntryOptions options, CancellationToken token = default)
        {
            Set(key, value, options);
            return Task.CompletedTask;
        }

        public void Refresh(string key)
        {
        }

        public Task RefreshAsync(string key, CancellationToken token = default) => Task.CompletedTask;

        public void Remove(string key) => _entries.TryRemove(key, out _);

        public Task RemoveAsync(string key, CancellationToken token = default)
        {
            Remove(key);
            return Task.CompletedTask;
        }
    }
}
