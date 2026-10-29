using Shouldly;
using TravelChat.Query.Features.Flights;
using TravelChat.Query.Features.Hotels;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

public sealed class HotelsServiceTests
{
    private const string Body = """
        {
          "properties": [
            { "name": "Grand Hotel", "link": "https://grand.example/book", "property_token": "p1", "hotel_class": "5-star hotel", "overall_rating": 4.6,
              "total_rate": { "extracted_lowest": 540 }, "rate_per_night": { "extracted_lowest": 180 } },
            { "name": "Night Only", "link": "https://night.example/book", "property_token": "p2",
              "rate_per_night": { "extracted_lowest": 100 } },
            { "name": "Insecure", "link": "http://insecure.example/book", "total_rate": { "extracted_lowest": 50 } },
            { "name": "No Link", "total_rate": { "extracted_lowest": 60 } },
            { "name": "No Price", "link": "https://noprice.example/book" }
          ]
        }
        """;

    private static readonly Dictionary<string, string?> s_hotelKey = new() { ["ProviderKeys:hotels:serpapi"] = "secret" };

    private static readonly DateOnly s_checkIn = new(2026, 12, 1);
    private static readonly DateOnly s_checkOut = new(2026, 12, 4);

    [Fact]
    public async Task OptionsCarryUsdStayTotalsAndPropertyLink()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_hotelKey);
        var handler = new StubHandler(_ => StubHandler.Json(Body));
        var service = new HotelsService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("Paris", s_checkIn, s_checkOut, 2, ct);

        result.Status.ShouldBe(ProviderStatus.Ok);
        var options = result.Data.ShouldNotBeNull();
        options.Count.ShouldBe(2);
        options[0].PriceUsd.ShouldBe(540m);
        options[0].DeepLink.ShouldBe("https://grand.example/book");
        options[0].Summary.ShouldBe("Grand Hotel, 5-star hotel, rated 4.6");
        options[1].PriceUsd.ShouldBe(300m);
        options[1].DeepLink.ShouldBe("https://night.example/book");
    }

    [Fact]
    public async Task RequestUsesStayDatesUsdAndTravellers()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_hotelKey);
        var handler = new StubHandler(_ => StubHandler.Json(Body));
        var service = new HotelsService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.SearchAsync("Paris", s_checkIn, s_checkOut, 2, ct);

        var query = handler.Requests.Single().Query;
        query.ShouldContain("engine=google_hotels");
        query.ShouldContain("q=Paris");
        query.ShouldContain("check_in_date=2026-12-01");
        query.ShouldContain("check_out_date=2026-12-04");
        query.ShouldContain("currency=USD");
        query.ShouldContain("adults=2");
    }

    [Fact]
    public async Task MissingHotelsKeyIsUnavailableWhileFlightsStillAnswerOk()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(new Dictionary<string, string?>
        {
            ["ProviderKeys:hotels:serpapi"] = string.Empty,
            ["ProviderKeys:flights:serpapi"] = "secret",
        });
        var hotelsHandler = new StubHandler(_ => StubHandler.Json(Body));
        var flightsHandler = new StubHandler(_ => StubHandler.Json("""{"best_flights":[]}"""));
        var hotels = new HotelsService(new StubHttpClientFactory(hotelsHandler), rig.Fetch);
        var flights = new FlightsService(new StubHttpClientFactory(flightsHandler), rig.Fetch);

        var hotelResult = await hotels.SearchAsync("Paris", s_checkIn, s_checkOut, 1, ct);
        var flightResult = await flights.SearchAsync("SGN", "CDG", s_checkIn, s_checkOut, 1, ct);

        hotelResult.Status.ShouldBe(ProviderStatus.Unavailable);
        hotelsHandler.Calls.ShouldBe(0);
        flightResult.Status.ShouldBe(ProviderStatus.Ok);
    }

    [Fact]
    public async Task QuotaBodyMapsToLimit()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_hotelKey);
        var handler = new StubHandler(_ => StubHandler.Json("""{"error":"Your account has run out of searches."}"""));
        var service = new HotelsService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("Paris", s_checkIn, s_checkOut, 1, ct);

        result.Status.ShouldBe(ProviderStatus.Limit);
    }
}
