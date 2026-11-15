using Shouldly;
using TravelChat.Query.Features.Places;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

public sealed class PlacesServiceTests
{
    private const string GeocodeBody = """{"results":[{"lon":2.35,"lat":48.85}]}""";

    private const string PlacesBody =
        """
        {"features":[
          {"properties":{"name":"Louvre","lat":48.8606,"lon":2.3376,"categories":["entertainment.museum","tourism"]}},
          {"properties":{"name":"Out Of Range","lat":123.0,"lon":2.0}},
          {"properties":{"name":"No Coordinates"}},
          {"properties":{"categories":["x"]}}]}
        """;

    private static readonly Dictionary<string, string?> s_withKey = new() { ["ProviderKeys:places:geoapify"] = "secret" };

    [Fact]
    public async Task MissingKeyReturnsUnavailableWithoutHandlerCall()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(new Dictionary<string, string?> { ["ProviderKeys:places:geoapify"] = string.Empty });
        var handler = new StubHandler(Route);
        var service = new PlacesService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.GetAttractionsAsync("Paris", ["food"], ct);

        result.Status.ShouldBe(ProviderStatus.Unavailable);
        handler.Calls.ShouldBe(0);
    }

    [Fact]
    public async Task PlacesAreMappedAndNamelessFeaturesAreSkipped()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var service = new PlacesService(new StubHttpClientFactory(new StubHandler(Route)), rig.Fetch);

        var result = await service.GetAttractionsAsync("Paris", ["museums"], ct);

        result.Status.ShouldBe(ProviderStatus.Ok);
        var attractions = result.Data.ShouldNotBeNull();
        attractions.Count.ShouldBe(3);
        attractions[0].ShouldBe(new Attraction("Louvre", "entertainment.museum", 48.8606, 2.3376));
    }

    [Fact]
    public async Task OutOfRangeOrMissingCoordinatesAreNull()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var service = new PlacesService(new StubHttpClientFactory(new StubHandler(Route)), rig.Fetch);

        var result = await service.GetAttractionsAsync("Paris", ["museums"], ct);

        var attractions = result.Data.ShouldNotBeNull();
        attractions[1].ShouldBe(new Attraction("Out Of Range", "attraction", null, null));
        attractions[2].ShouldBe(new Attraction("No Coordinates", "attraction", null, null));
    }

    [Fact]
    public async Task InterestsAreSentAsMappedCategories()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(Route);
        var service = new PlacesService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.GetAttractionsAsync("Paris", ["food"], ct);

        var placesRequest = handler.Requests.Single(r => r.AbsolutePath == "/v2/places");
        Uri.UnescapeDataString(placesRequest.Query).ShouldContain("categories=catering&");
    }

    [Fact]
    public async Task RepeatedQueryWithReorderedInterestsDoesNotCallTheProviderAgain()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(Route);
        var service = new PlacesService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.GetAttractionsAsync("Paris", ["food", "hiking"], ct);
        var callsAfterFirst = handler.Calls;
        await service.GetAttractionsAsync(" paris", ["Hiking", "FOOD"], ct);

        handler.Calls.ShouldBe(callsAfterFirst);
    }

    [Fact]
    public void FoodMapsToCatering()
    {
        Joined(PlacesService.CategoriesFor(["food"])).ShouldBe("catering");
    }

    [Fact]
    public void HikingMapsToNaturalAndLeisure()
    {
        Joined(PlacesService.CategoriesFor(["hiking"])).ShouldBe("leisure,natural");
    }

    [Theory]
    [InlineData("unicorn-spotting")]
    [InlineData("")]
    public void UnknownOrEmptyInterestMapsToTourismAttraction(string interest)
    {
        Joined(PlacesService.CategoriesFor([interest])).ShouldBe("tourism.attraction");
    }

    [Fact]
    public void NoInterestsMapToTourismAttraction()
    {
        Joined(PlacesService.CategoriesFor([])).ShouldBe("tourism.attraction");
    }

    private static string Joined(IReadOnlyList<string> categories) => string.Join(',', categories);

    private static HttpResponseMessage Route(HttpRequestMessage request) =>
        request.RequestUri?.AbsolutePath == "/v1/geocode/search"
            ? StubHandler.Json(GeocodeBody)
            : StubHandler.Json(PlacesBody);
}
