using System.Net;
using Shouldly;
using TravelChat.Query.Features.Flights;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

public sealed class FlightsServiceTests
{
    private const string Body = """
        {
          "search_metadata": { "google_flights_url": "https://www.google.com/travel/flights?q=abc" },
          "best_flights": [
            {
              "flights": [
                { "airline": "KLM", "departure_airport": { "id": "SGN", "time": "2026-12-01 08:00" }, "arrival_airport": { "id": "AMS", "time": "2026-12-01 14:30" } }
              ],
              "price": 1234,
              "booking_token": "tok-1"
            }
          ],
          "other_flights": [
            {
              "flights": [
                { "airline": "Qatar", "departure_airport": { "time": "2026-12-01 09:00" }, "arrival_airport": { "time": "2026-12-01 12:00" } },
                { "airline": "Qatar", "departure_airport": { "time": "2026-12-01 13:00" }, "arrival_airport": { "time": "2026-12-01 20:00" } }
              ],
              "price": 987.5,
              "departure_token": "tok-2"
            },
            { "flights": [], "booking_token": "no-price" }
          ]
        }
        """;

    private static readonly Dictionary<string, string?> s_withKey = new() { ["ProviderKeys:flights:serpapi"] = "secret" };

    private static readonly DateOnly s_depart = new(2026, 12, 1);
    private static readonly DateOnly s_return = new(2026, 12, 10);

    [Fact]
    public async Task OptionsCarryUsdTotalsAndDeepLink()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json(Body));
        var service = new FlightsService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("sgn", "ams", s_depart, s_return, 2, ct);

        result.Status.ShouldBe(ProviderStatus.Ok);
        var options = result.Data.ShouldNotBeNull();
        options.Count.ShouldBe(2);
        options[0].PriceUsd.ShouldBe(1234m);
        options[0].DeepLink.ShouldBe("https://www.google.com/travel/flights?q=abc");
        options[0].Summary.ShouldBe("KLM, nonstop, 2026-12-01 08:00 - 2026-12-01 14:30");
        options[0].Id.Length.ShouldBe(12);
        options[1].PriceUsd.ShouldBe(987.5m);
        options[1].Summary.ShouldContain("1 stop");
        options[1].Id.ShouldNotBe(options[0].Id);
    }

    [Fact]
    public async Task RequestUsesRoundTripUsdAndTravellers()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json(Body));
        var service = new FlightsService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.SearchAsync("sgn", "ams", s_depart, s_return, 2, ct);

        var query = handler.Requests.Single().Query;
        query.ShouldContain("engine=google_flights");
        query.ShouldContain("departure_id=SGN");
        query.ShouldContain("arrival_id=AMS");
        query.ShouldContain("outbound_date=2026-12-01");
        query.ShouldContain("return_date=2026-12-10");
        query.ShouldContain("currency=USD");
        query.ShouldContain("adults=2");
    }

    [Theory]
    [InlineData("tyo", "HND%2CNRT")]
    [InlineData("LON", "LHR%2CLGW%2CSTN%2CLTN%2CLCY%2CSEN")]
    [InlineData("hnd", "HND")]
    public async Task CityCodeIsSentAsItsAirports(string destination, string expected)
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json(Body));
        var service = new FlightsService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.SearchAsync("nyc", destination, s_depart, s_return, 1, ct);

        var query = handler.Requests.Single().Query;
        query.ShouldContain("departure_id=JFK%2CEWR%2CLGA");
        query.ShouldContain($"arrival_id={expected}&");
    }

    [Fact]
    public async Task NonHttpsLinkDropsEveryOption()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var body = Body.Replace("https://www.google.com", "http://www.google.com", StringComparison.Ordinal);
        var service = new FlightsService(new StubHttpClientFactory(new StubHandler(_ => StubHandler.Json(body))), rig.Fetch);

        var result = await service.SearchAsync("SGN", "AMS", s_depart, s_return, 1, ct);

        result.Status.ShouldBe(ProviderStatus.Ok);
        result.Data.ShouldNotBeNull().ShouldBeEmpty();
    }

    [Fact]
    public async Task OutOfSearchesBodyMapsToLimit()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json("""{"error":"Your account has run out of searches."}"""));
        var service = new FlightsService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("SGN", "AMS", s_depart, s_return, 1, ct);

        result.Status.ShouldBe(ProviderStatus.Limit);
    }

    [Fact]
    public async Task MissingKeyReturnsUnavailableWithoutHandlerCall()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(new Dictionary<string, string?> { ["ProviderKeys:flights:serpapi"] = string.Empty });
        var handler = new StubHandler(_ => StubHandler.Json(Body));
        var service = new FlightsService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("SGN", "AMS", s_depart, s_return, 1, ct);

        result.Status.ShouldBe(ProviderStatus.Unavailable);
        handler.Calls.ShouldBe(0);
    }

    [Fact]
    public async Task ServerErrorMapsToError()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json("{}", HttpStatusCode.BadRequest));
        var service = new FlightsService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("SGN", "AMS", s_depart, s_return, 1, ct);

        result.Status.ShouldBe(ProviderStatus.Error);
    }

    [Fact]
    public async Task RepeatedSearchIsServedFromCache()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json(Body));
        var service = new FlightsService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.SearchAsync("SGN", "AMS", s_depart, s_return, 2, ct);
        await service.SearchAsync(" sgn", "ams ", s_depart, s_return, 2, ct);

        handler.Calls.ShouldBe(1);
    }
}
