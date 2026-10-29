using Shouldly;
using TravelChat.Query.Features.Shared;
using TravelChat.Query.Features.Weather;

namespace TravelChat.Query.Tests;

public sealed class WeatherServiceTests
{
    private const string GeocodeBody = """{"results":[{"name":"Paris","latitude":48.85,"longitude":2.35}]}""";

    private const string ForecastBody =
        """{"daily":{"time":["2026-10-27","2026-10-28"],"temperature_2m_max":[18.5,17.0],"temperature_2m_min":[9.1,8.0],"precipitation_probability_max":[20,65]}}""";

    private static readonly DateTimeOffset s_now = new(2026, 10, 27, 9, 0, 0, TimeSpan.Zero);

    [Theory]
    [InlineData("2026-12-01", "2026-12-05")]
    [InlineData("2026-10-01", "2026-10-10")]
    public async Task RangeOutsideForecastWindowMakesNoCall(string start, string end)
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(Route);
        var service = CreateService(rig, handler);

        var result = await service.GetForecastAsync("Paris", DateOnly.Parse(start, System.Globalization.CultureInfo.InvariantCulture), DateOnly.Parse(end, System.Globalization.CultureInfo.InvariantCulture), ct);

        handler.Calls.ShouldBe(0);
        result.Status.ShouldBe(ProviderStatus.Ok);
        result.Data.ShouldNotBeNull().Days.ShouldBeEmpty();
    }

    [Fact]
    public async Task PartiallyOverlappingRangeIsClampedToTheWindow()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(Route);
        var service = CreateService(rig, handler);

        var result = await service.GetForecastAsync("Paris", new DateOnly(2026, 10, 25), new DateOnly(2026, 12, 1), ct);

        var forecastRequest = handler.Requests.Single(r => r.Host == "api.open-meteo.com");
        forecastRequest.Query.ShouldContain("start_date=2026-10-27");
        forecastRequest.Query.ShouldContain("end_date=2026-11-11");
        result.Status.ShouldBe(ProviderStatus.Ok);
    }

    [Fact]
    public async Task ForecastIsMappedToDays()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var service = CreateService(rig, new StubHandler(Route));

        var result = await service.GetForecastAsync("Paris", new DateOnly(2026, 10, 27), new DateOnly(2026, 10, 28), ct);

        var forecast = result.Data.ShouldNotBeNull();
        forecast.City.ShouldBe("Paris");
        forecast.Days.Count.ShouldBe(2);
        forecast.Days[0].ShouldBe(new ForecastDay("2026-10-27", 18.5, 9.1, 20));
        forecast.Days[1].RainChancePct.ShouldBe(65);
    }

    [Fact]
    public async Task RepeatedQueryDoesNotCallTheProviderAgain()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var handler = new StubHandler(Route);
        var service = CreateService(rig, handler);

        await service.GetForecastAsync("Paris", new DateOnly(2026, 10, 27), new DateOnly(2026, 10, 28), ct);
        var callsAfterFirst = handler.Calls;
        await service.GetForecastAsync("  paris ", new DateOnly(2026, 10, 27), new DateOnly(2026, 10, 28), ct);

        handler.Calls.ShouldBe(callsAfterFirst);
    }

    [Fact]
    public async Task UnknownCityReturnsError()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig();
        var service = CreateService(rig, new StubHandler(_ => StubHandler.Json("{}")));

        var result = await service.GetForecastAsync("Nowhereville", new DateOnly(2026, 10, 27), new DateOnly(2026, 10, 28), ct);

        result.Status.ShouldBe(ProviderStatus.Error);
        result.Message.ShouldBe("city not found");
    }

    private static HttpResponseMessage Route(HttpRequestMessage request) =>
        request.RequestUri?.Host == "geocoding-api.open-meteo.com"
            ? StubHandler.Json(GeocodeBody)
            : StubHandler.Json(ForecastBody);

    private static WeatherService CreateService(CacheRig rig, StubHandler handler) =>
        new(new StubHttpClientFactory(handler), rig.Fetch, new FixedTimeProvider(s_now));
}
