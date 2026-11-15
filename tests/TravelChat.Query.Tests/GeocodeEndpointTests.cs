using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Shouldly;
using TravelChat.Query.Features.Geocode;

namespace TravelChat.Query.Tests;

internal sealed class QueryApiFactory(StubHandler geoapify) : WebApplicationFactory<Program>
{
    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureAppConfiguration((_, configuration) =>
            configuration.AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["ProviderKeys:geocode:geoapify"] = "test-key",
            }));

        builder.ConfigureTestServices(services =>
            services.AddHttpClient(GeocodeService.ClientName).ConfigurePrimaryHttpMessageHandler(() => geoapify));
    }
}

public sealed class GeocodeEndpointTests
{
    private static readonly string s_longPlace = new('x', 201);

    [Theory]
    [InlineData("")]
    [InlineData("?place=")]
    [InlineData("?place=%20%20")]
    public async Task EmptyOrBlankPlaceIsBadRequest(string query)
    {
        var status = await GetStatusAsync($"/geocode/places{query}");

        status.ShouldBe(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task MoreThanThirtyPlacesIsBadRequest()
    {
        var query = string.Join('&', Enumerable.Range(0, 31).Select(i => $"place=p{i}"));

        var status = await GetStatusAsync($"/geocode/places?{query}");

        status.ShouldBe(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task PlaceLongerThanTwoHundredCharactersIsBadRequest()
    {
        var status = await GetStatusAsync($"/geocode/places?place=ok&place={s_longPlace}");

        status.ShouldBe(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task ThirtyPlacesAreAccepted()
    {
        var query = string.Join('&', Enumerable.Range(0, 30).Select(i => $"place=p{i}"));

        var status = await GetStatusAsync($"/geocode/places?{query}");

        status.ShouldBe(HttpStatusCode.OK);
    }

    [Fact]
    public async Task RepeatedPlaceValuesAreBoundAndEchoed()
    {
        var ct = TestContext.Current.CancellationToken;
        var handler = new StubHandler(_ => StubHandler.Json("""{"results":[{"lat":35.5,"lon":139.7}]}"""));
        await using var factory = new QueryApiFactory(handler);
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(
            new Uri("/geocode/places?place=Haneda%20Airport%2C%20Tokyo&place=Senso-ji%2C%20Tokyo", UriKind.Relative),
            ct);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
        body.GetProperty("status").GetString().ShouldBe("ok");
        var names = body.GetProperty("data").EnumerateArray().Select(item => item.GetProperty("name").GetString()).ToArray();
        names.Length.ShouldBe(2);
        names.ShouldContain("Haneda Airport, Tokyo");
        names.ShouldContain("Senso-ji, Tokyo");
        handler.Calls.ShouldBe(2);
    }

    private static async Task<HttpStatusCode> GetStatusAsync(string pathAndQuery)
    {
        var ct = TestContext.Current.CancellationToken;
        var handler = new StubHandler(_ => StubHandler.Json("""{"results":[]}"""));
        await using var factory = new QueryApiFactory(handler);
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(new Uri(pathAndQuery, UriKind.Relative), ct);

        return response.StatusCode;
    }
}
