using System.Net;
using Shouldly;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

public sealed class CachedFetchTests
{
    private const string Client = "weather__open_meteo";
    private const string KeyedClient = "places__geoapify";
    private static readonly TimeSpan s_ttl = TimeSpan.FromMinutes(5);
    private static readonly Uri s_endpoint = new("https://provider.invalid/data");

    [Fact]
    public async Task IdenticalQueryTwiceCallsHandlerOnce()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(_ => StubHandler.Json("""{"value":1}"""));
        var fetch = FetchFrom(handler);

        var first = await rig.Fetch.GetAsync(Client, "Paris|2026-11-01", s_ttl, fetch, ct);
        var second = await rig.Fetch.GetAsync(Client, "Paris|2026-11-01", s_ttl, fetch, ct);

        handler.Calls.ShouldBe(1);
        first.Status.ShouldBe(ProviderStatus.Ok);
        second.Status.ShouldBe(ProviderStatus.Ok);
        second.Data.ShouldBe(first.Data);
    }

    [Fact]
    public async Task QueryDifferingOnlyInCaseOrSpacingCallsHandlerOnce()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(_ => StubHandler.Json("""{"value":1}"""));
        var fetch = FetchFrom(handler);

        await rig.Fetch.GetAsync(Client, "New York|2026-11-01", s_ttl, fetch, ct);
        await rig.Fetch.GetAsync(Client, "  new   YORK |2026-11-01", s_ttl, fetch, ct);

        handler.Calls.ShouldBe(1);
    }

    [Fact]
    public async Task DifferentQueriesEachCallHandler()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(_ => StubHandler.Json("""{"value":1}"""));
        var fetch = FetchFrom(handler);

        await rig.Fetch.GetAsync(Client, "paris", s_ttl, fetch, ct);
        await rig.Fetch.GetAsync(Client, "rome", s_ttl, fetch, ct);

        handler.Calls.ShouldBe(2);
    }

    [Fact]
    public async Task FailureIsNotCached()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var attempts = 0;
        var handler = new StubHandler(_ => ++attempts == 1
            ? StubHandler.Json("{}", HttpStatusCode.InternalServerError)
            : StubHandler.Json("""{"value":1}"""));
        var fetch = FetchFrom(handler);

        var failed = await rig.Fetch.GetAsync(Client, "paris", s_ttl, fetch, ct);
        var recovered = await rig.Fetch.GetAsync(Client, "paris", s_ttl, fetch, ct);
        var cached = await rig.Fetch.GetAsync(Client, "paris", s_ttl, fetch, ct);

        failed.Status.ShouldBe(ProviderStatus.Error);
        recovered.Status.ShouldBe(ProviderStatus.Ok);
        cached.Status.ShouldBe(ProviderStatus.Ok);
        handler.Calls.ShouldBe(2);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public async Task MissingKeyReturnsUnavailableWithoutHandlerCall(string? configured)
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(new Dictionary<string, string?> { ["ProviderKeys:places:geoapify"] = configured });
        var handler = new StubHandler(_ => StubHandler.Json("{}"));

        var result = await rig.Fetch.GetAsync(KeyedClient, "paris", s_ttl, FetchFrom(handler), ct, requiresKey: true);

        result.Status.ShouldBe(ProviderStatus.Unavailable);
        result.Message.ShouldNotBeNullOrWhiteSpace();
        handler.Calls.ShouldBe(0);
    }

    [Fact]
    public async Task ConfiguredKeyIsPassedToFetch()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(new Dictionary<string, string?> { ["ProviderKeys:places:geoapify"] = "secret" });

        var result = await rig.Fetch.GetAsync(
            KeyedClient,
            "paris",
            s_ttl,
            (key, _) => Task.FromResult(key ?? "none"),
            ct,
            requiresKey: true);

        result.Data.ShouldBe("secret");
    }

    [Fact]
    public async Task TooManyRequestsReturnsLimit()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(_ => StubHandler.Json("{}", HttpStatusCode.TooManyRequests));

        var result = await rig.Fetch.GetAsync(Client, "paris", s_ttl, FetchFrom(handler), ct);

        result.Status.ShouldBe(ProviderStatus.Limit);
        handler.Calls.ShouldBe(1);
    }

    [Theory]
    [InlineData("""{"error":"Your account has run out of searches."}""", HttpStatusCode.OK)]
    [InlineData("""{"message":"Monthly quota exceeded"}""", HttpStatusCode.Forbidden)]
    [InlineData("""{"error":true,"reason":"Daily API request limit exceeded."}""", HttpStatusCode.BadRequest)]
    public async Task QuotaBodyReturnsLimit(string body, HttpStatusCode status)
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(_ => StubHandler.Json(body, status));

        var result = await rig.Fetch.GetAsync(Client, "paris", s_ttl, FetchFrom(handler), ct);

        result.Status.ShouldBe(ProviderStatus.Limit);
    }

    [Theory]
    [InlineData(HttpStatusCode.InternalServerError)]
    [InlineData(HttpStatusCode.BadGateway)]
    [InlineData(HttpStatusCode.ServiceUnavailable)]
    public async Task ServerErrorReturnsError(HttpStatusCode status)
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(_ => StubHandler.Json("{}", status));

        var result = await rig.Fetch.GetAsync(Client, "paris", s_ttl, FetchFrom(handler), ct);

        result.Status.ShouldBe(ProviderStatus.Error);
        result.Data.ShouldBeNull();
    }

    [Fact]
    public async Task TimeoutReturnsError()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(_ => throw new TaskCanceledException("timed out"));

        var result = await rig.Fetch.GetAsync(Client, "paris", s_ttl, FetchFrom(handler), ct);

        result.Status.ShouldBe(ProviderStatus.Error);
    }

    [Fact]
    public async Task ConnectionFailureReturnsError()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(_ => throw new HttpRequestException("connection refused"));

        var result = await rig.Fetch.GetAsync(Client, "paris", s_ttl, FetchFrom(handler), ct);

        result.Status.ShouldBe(ProviderStatus.Error);
    }

    [Fact]
    public async Task ProviderCallIsLoggedOnlyOnCacheMiss()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(_ => StubHandler.Json("""{"value":1}"""));
        var fetch = FetchFrom(handler);

        await rig.Fetch.GetAsync(Client, "paris", s_ttl, fetch, ct);
        await rig.Fetch.GetAsync(Client, "Paris ", s_ttl, fetch, ct);

        rig.Logger.Messages.Where(m => m.Contains("Provider call", StringComparison.Ordinal))
            .ShouldHaveSingleItem()
            .ShouldBe($"Information: Provider call {Client}");
    }

    [Fact]
    public void NormaliseListSortsDeduplicatesAndLowerCases()
    {
        CachedFetch.NormaliseList(["Hiking", " food ", "hiking", ""]).ShouldBe("food,hiking");
    }

    [Fact]
    public void NormaliseDateUsesIsoFormat()
    {
        CachedFetch.Normalise(new DateOnly(2026, 3, 4)).ShouldBe("2026-03-04");
    }

    private static Func<string?, CancellationToken, Task<string>> FetchFrom(HttpMessageHandler handler)
    {
        var factory = new StubHttpClientFactory(handler);
        return async (_, token) =>
        {
            var http = factory.CreateClient(Client);
            using var response = await http.GetAsync(s_endpoint, token);
            return await ResultMapping.ReadSuccessBodyAsync(response, token);
        };
    }
}
