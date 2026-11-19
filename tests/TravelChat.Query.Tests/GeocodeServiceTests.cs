using System.Net;
using Shouldly;
using TravelChat.Query.Features.Geocode;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

public sealed class GeocodeServiceTests
{
    private static readonly Dictionary<string, string?> s_withKey = new() { ["ProviderKeys:geocode:geoapify"] = "secret" };

    [Fact]
    public async Task FoundPlacesEchoTheInputTextAndNotFoundOnesAreLeftOut()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var service = new GeocodeService(new StubHttpClientFactory(new StubHandler(Route)), rig.Fetch);

        var result = await service.GeocodeAsync(["Haneda Airport, Tokyo", "Nowhere"], ct);

        result.Status.ShouldBe(ProviderStatus.Ok);
        var points = result.Data.ShouldNotBeNull();
        points.Count.ShouldBe(1);
        points[0].ShouldBe(new GeoPoint("Haneda Airport, Tokyo", 35.5494, 139.7798));
    }

    [Fact]
    public async Task MissingKeyIsUnavailableWithoutAnyCall()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(new Dictionary<string, string?> { ["ProviderKeys:geocode:geoapify"] = string.Empty });
        var handler = new StubHandler(Route);
        var service = new GeocodeService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.GeocodeAsync(["Haneda Airport, Tokyo"], ct);

        result.Status.ShouldBe(ProviderStatus.Unavailable);
        handler.Calls.ShouldBe(0);
    }

    [Fact]
    public async Task OnePlaceHittingTheLimitMakesTheWholeResultLimit()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(request => request.RequestUri!.Query.Contains("Nowhere", StringComparison.Ordinal)
            ? StubHandler.Json("{}", HttpStatusCode.TooManyRequests)
            : Route(request));
        var service = new GeocodeService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.GeocodeAsync(["Haneda Airport, Tokyo", "Nowhere"], ct);

        result.Status.ShouldBe(ProviderStatus.Limit);
    }

    [Fact]
    public async Task ErrorOnASinglePlaceIsDropped()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(request => request.RequestUri!.Query.Contains("Broken", StringComparison.Ordinal)
            ? StubHandler.Json("{}", HttpStatusCode.InternalServerError)
            : Route(request));
        var service = new GeocodeService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.GeocodeAsync(["Broken", "Haneda Airport, Tokyo"], ct);

        result.Status.ShouldBe(ProviderStatus.Ok);
        var points = result.Data.ShouldNotBeNull();
        points.Count.ShouldBe(1);
        points[0].Name.ShouldBe("Haneda Airport, Tokyo");
    }

    [Fact]
    public async Task EachDistinctPlaceIsFetchedOnceAndCached()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(Route);
        var service = new GeocodeService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.GeocodeAsync(["Haneda Airport, Tokyo", "Haneda Airport, Tokyo"], ct);
        await service.GeocodeAsync([" haneda airport,  tokyo"], ct);

        handler.Calls.ShouldBe(1);
    }

    [Fact]
    public async Task RequestAsksForOneResult()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(Route);
        var service = new GeocodeService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.GeocodeAsync(["Haneda Airport, Tokyo"], ct);

        var request = handler.Requests.Single();
        request.AbsolutePath.ShouldBe("/v1/geocode/search");
        request.Query.ShouldContain("limit=1");
    }

    [Fact]
    public async Task OutOfRangeCoordinatesCountAsNotFound()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json("""{"results":[{"lat":99.0,"lon":10.0}]}"""));
        var service = new GeocodeService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.GeocodeAsync(["Somewhere"], ct);

        result.Data.ShouldNotBeNull().ShouldBeEmpty();
    }

    [Fact]
    public void MapCoordinatesNonObjectRootThrowsErrorStatus()
    {
        var failure = Should.Throw<ProviderFailureException>(() => GeocodeService.MapCoordinates("[]"));

        failure.Status.ShouldBe("error");
    }

    private static HttpResponseMessage Route(HttpRequestMessage request) =>
        request.RequestUri!.Query.Contains("Haneda", StringComparison.OrdinalIgnoreCase)
            ? StubHandler.Json("""{"results":[{"lat":35.5494,"lon":139.7798}]}""")
            : StubHandler.Json("""{"results":[]}""");
}
