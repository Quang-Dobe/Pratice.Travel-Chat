using System.Net;
using System.Net.Http.Headers;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Shouldly;
using TravelChat.Gateway.Features.Maps;

namespace TravelChat.Gateway.Tests;

public sealed class MapsProxyTests : IClassFixture<GatewayFactory>
{
    private const string Key = "secret-key-123";
    private const string PublicBase = "http://localhost:8080/api/maps";
    private const string StylePath = "/api/maps/v1/styles/osm-bright/style.json";

    private readonly GatewayFactory _factory;

    public MapsProxyTests(GatewayFactory factory)
    {
        _factory = factory;
        _factory.Destinations.Reset();
    }

    [Fact]
    public void RewriteSwapsTheHostAndRemovesTheKeyFromStyleUrls()
    {
        var body = $$"""
            {
              "sprite": "https://maps.geoapify.com/v1/styles/osm-bright/sprite?apiKey={{Key}}",
              "glyphs": "https://maps.geoapify.com/v1/styles/osm-bright/fonts/{fontstack}/{range}.pbf?apiKey={{Key}}",
              "sources": { "default": { "type": "vector", "url": "https://maps.geoapify.com/v1/styles/osm-bright/data.json?apiKey={{Key}}" } }
            }
            """;

        var result = MapsProxy.Rewrite(body, Key, PublicBase);

        result.ShouldContain("\"http://localhost:8080/api/maps/v1/styles/osm-bright/sprite\"");
        result.ShouldContain("\"http://localhost:8080/api/maps/v1/styles/osm-bright/fonts/{fontstack}/{range}.pbf\"");
        result.ShouldContain("\"http://localhost:8080/api/maps/v1/styles/osm-bright/data.json\"");
        result.ShouldNotContain("apiKey");
        result.ShouldNotContain(Key);
        result.ShouldNotContain("maps.geoapify.com");
    }

    [Fact]
    public void RewriteHandlesTileJsonTilesArray()
    {
        var body = $$"""
            {"tiles":["https://maps.geoapify.com/v1/tile/carto/{z}/{x}/{y}.pbf?apiKey={{Key}}&lang=en","https://maps.geoapify.com/v1/tile/carto/{z}/{x}/{y}.pbf?lang=en&apiKey={{Key}}"]}
            """;

        var result = MapsProxy.Rewrite(body, Key, PublicBase);

        result.ShouldContain("http://localhost:8080/api/maps/v1/tile/carto/{z}/{x}/{y}.pbf?lang=en\"");
        result.ShouldNotContain("apiKey");
        result.ShouldNotContain(Key);
    }

    [Fact]
    public void RewriteOnlySwapsTheHostWhenTheBodyHasNoKey()
    {
        var body = "{\"a\":\"https://maps.geoapify.com/v1/x\",\"b\":\"https://example.com/y?z=1\"}";

        var result = MapsProxy.Rewrite(body, Key, PublicBase);

        result.ShouldBe("{\"a\":\"http://localhost:8080/api/maps/v1/x\",\"b\":\"https://example.com/y?z=1\"}");
    }

    [Theory]
    [InlineData("application/json", true)]
    [InlineData("application/geo+json", true)]
    [InlineData("APPLICATION/JSON", true)]
    [InlineData("application/x-protobuf", false)]
    [InlineData("image/png", false)]
    [InlineData(null, false)]
    public void IsJsonMatchesJsonMediaTypesOnly(string? mediaType, bool expected) =>
        MapsProxy.IsJson(mediaType).ShouldBe(expected);

    [Fact]
    public async Task UnauthenticatedMapsRequestReturns401AndNeverCallsUpstream()
    {
        using var factory = WithKey(Key);
        using var client = NewClient(factory);

        using var response = await client.GetAsync(StylePath, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.Unauthorized);
        _factory.Destinations.Requests.ShouldBeEmpty();
    }

    [Fact]
    public async Task EmptyKeyReturns503AndNeverCallsUpstream()
    {
        using var factory = WithKey(string.Empty);
        using var client = NewClient(factory);
        using var request = await SignedInRequest(client, HttpMethod.Get, StylePath);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.ServiceUnavailable);
        _factory.Destinations.Requests.ShouldBeEmpty();
    }

    [Fact]
    public async Task ClientApiKeyIsDroppedAndTheConfiguredKeyIsAppendedOnce()
    {
        using var factory = WithKey(Key);
        using var client = NewClient(factory);
        using var request = await SignedInRequest(client, HttpMethod.Get, $"{StylePath}?apiKey=evil&lang=en");
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", "client-supplied");

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var seen = _factory.Destinations.Requests.ShouldHaveSingleItem();
        seen.Uri.Host.ShouldBe("maps.geoapify.com");
        seen.Uri.AbsolutePath.ShouldBe("/v1/styles/osm-bright/style.json");
        seen.Uri.Query.ShouldContain($"apiKey={Key}");
        seen.Uri.Query.ShouldContain("lang=en");
        seen.Uri.Query.ShouldNotContain("evil");
        seen.Uri.Query.Split("apiKey").Length.ShouldBe(2);
        seen.HasCookie.ShouldBeFalse();
        seen.Authorization.ShouldBeNull();
    }

    [Fact]
    public async Task JsonResponseIsRewrittenWithCorrectLengthAndBinaryStreamsThrough()
    {
        var upstreamJson = $$"""{"sprite":"https://maps.geoapify.com/v1/sprite?apiKey={{Key}}"}""";
        byte[] binary = [0x1, 0x2, 0x3, 0x4];
        _factory.Destinations.Respond = request =>
        {
            if (request.RequestUri!.AbsolutePath.EndsWith(".pbf", StringComparison.Ordinal))
            {
                var content = new ByteArrayContent(binary);
                content.Headers.ContentType = new MediaTypeHeaderValue("application/x-protobuf");
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
            }

            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(upstreamJson, System.Text.Encoding.UTF8, "application/json"),
            };
        };
        using var factory = WithKey(Key);
        using var client = NewClient(factory);

        using var jsonRequest = await SignedInRequest(client, HttpMethod.Get, StylePath);
        using var jsonResponse = await client.SendAsync(jsonRequest, TestContext.Current.CancellationToken);
        var jsonBody = await jsonResponse.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);

        jsonResponse.StatusCode.ShouldBe(HttpStatusCode.OK);
        jsonBody.ShouldNotContain(Key);
        jsonBody.ShouldNotContain("apiKey");
        jsonBody.ShouldContain("/api/maps/v1/sprite");
        jsonBody.ShouldNotContain("maps.geoapify.com");
        jsonResponse.Content.Headers.ContentLength.ShouldBe(System.Text.Encoding.UTF8.GetByteCount(jsonBody));

        using var tileRequest = await SignedInRequest(client, HttpMethod.Get, "/api/maps/v1/tile/carto/1/1/1.pbf");
        using var tileResponse = await client.SendAsync(tileRequest, TestContext.Current.CancellationToken);

        tileResponse.StatusCode.ShouldBe(HttpStatusCode.OK);
        (await tileResponse.Content.ReadAsByteArrayAsync(TestContext.Current.CancellationToken)).ShouldBe(binary);
    }

    [Fact]
    public async Task MapsRouteSendsNoForwardedHeadersUpstream()
    {
        var forwarded = new List<string>();
        _factory.Destinations.Respond = request =>
        {
            forwarded.AddRange(request.Headers
                .Select(header => header.Key)
                .Where(name => name.StartsWith("X-Forwarded", StringComparison.OrdinalIgnoreCase)
                    || name.Equals("Forwarded", StringComparison.OrdinalIgnoreCase)));
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("ok") };
        };
        using var factory = WithKey(Key);
        using var client = NewClient(factory);
        using var request = await SignedInRequest(client, HttpMethod.Get, StylePath);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        _factory.Destinations.Requests.ShouldHaveSingleItem();
        forwarded.ShouldBeEmpty();
    }

    [Theory]
    [InlineData("GET", "/api/maps/v1/geocode/search?text=hanoi")]
    [InlineData("GET", "/api/maps/v1/routing?waypoints=1,1|2,2")]
    [InlineData("GET", "/api/maps/anything")]
    [InlineData("POST", "/api/maps/v1/styles/osm-bright/style.json")]
    [InlineData("POST", "/api/maps/v1/tile/carto/1/1/1.pbf")]
    public async Task OnlyGetOrHeadOnStylesAndTilesIsProxied(string method, string path)
    {
        using var factory = WithKey(Key);
        using var client = NewClient(factory);
        using var request = await SignedInRequest(client, new HttpMethod(method), path);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.NotFound);
        _factory.Destinations.Requests.ShouldBeEmpty();
    }

    [Fact]
    public async Task EncodedJsonUpstreamFailsClosedWith502()
    {
        _factory.Destinations.Respond = _ =>
        {
            var content = new StringContent("{}", System.Text.Encoding.UTF8, "application/json");
            content.Headers.ContentEncoding.Add("gzip");
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
        };
        using var factory = WithKey(Key);
        using var client = NewClient(factory);
        using var request = await SignedInRequest(client, HttpMethod.Get, StylePath);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.BadGateway);
    }

    [Fact]
    public async Task LocationHeaderIsRewritten()
    {
        _factory.Destinations.Respond = _ =>
        {
            var response = new HttpResponseMessage(HttpStatusCode.Found);
            response.Headers.Location = new Uri($"https://maps.geoapify.com/v1/tile/a.pbf?apiKey={Key}");
            return response;
        };
        using var factory = WithKey(Key);
        using var client = NewClient(factory);
        using var request = await SignedInRequest(client, HttpMethod.Get, "/api/maps/v1/tile/a.pbf");

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.Found);
        var location = response.Headers.Location.ShouldNotBeNull().OriginalString;
        location.ShouldBe("http://localhost/api/maps/v1/tile/a.pbf");
    }

    [Fact]
    public void RewriteAlsoRemovesTheUrlEncodedKey()
    {
        const string encodedKey = "a b+c";
        var body = $"\"https://maps.geoapify.com/x?apiKey={Uri.EscapeDataString(encodedKey)}&y=1\"";

        MapsProxy.Rewrite(body, encodedKey, PublicBase).ShouldBe($"\"{PublicBase}/x?y=1\"");
    }

    private WebApplicationFactory<Program> WithKey(string key) =>
        _factory.WithWebHostBuilder(builder =>
            builder.ConfigureAppConfiguration((_, configuration) =>
                configuration.AddInMemoryCollection(new Dictionary<string, string?>
                {
                    [$"{MapsOptions.SectionName}:{nameof(MapsOptions.ApiKey)}"] = key,
                })));

    private static HttpClient NewClient(WebApplicationFactory<Program> factory) =>
        factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false, HandleCookies = false });

    private static async Task<HttpRequestMessage> SignedInRequest(
        HttpClient client,
        HttpMethod method,
        string path)
    {
        var signIn = $"{GatewayFactory.TestEndpointsStartupFilter.SignInPath}?oid=oid-1&uid={Guid.NewGuid():D}";
        using var signInResponse = await client.GetAsync(
            new Uri(signIn, UriKind.Relative),
            TestContext.Current.CancellationToken);
        var cookie = string.Join(
            "; ",
            signInResponse.Headers.GetValues("Set-Cookie").Select(header => header.Split(';')[0]));

        var request = new HttpRequestMessage(method, new Uri(path, UriKind.Relative));
        request.Headers.Add("Cookie", cookie);
        return request;
    }
}
