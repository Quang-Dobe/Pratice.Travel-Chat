using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Shouldly;
using TravelChat.Query.Features.Fetch;
using TravelChat.Query.Features.Search;

namespace TravelChat.Query.Tests;

internal sealed class SearchFetchApiFactory(StubHandler serpapi, StubHandler pages) : WebApplicationFactory<Program>
{
    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureAppConfiguration((_, configuration) =>
            configuration.AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["ProviderKeys:search:serpapi"] = "test-key",
            }));

        builder.ConfigureTestServices(services =>
        {
            services.AddHttpClient(SearchService.ClientName).ConfigurePrimaryHttpMessageHandler(() => serpapi);
            services.AddHttpClient(FetchService.ClientName).ConfigurePrimaryHttpMessageHandler(() => pages);
        });
    }
}

public sealed class SearchFetchEndpointTests
{
    private static readonly string s_longQuery = new('q', 201);
    private static readonly string s_longUrl = "https://example.com/" + new string('p', 1981);

    [Theory]
    [InlineData("")]
    [InlineData("?q=")]
    [InlineData("?q=%20%20")]
    public async Task SearchWithEmptyOrBlankQueryIsBadRequest(string query)
    {
        var (status, serpapi, _) = await GetAsync($"/search/web{query}");

        status.ShouldBe(HttpStatusCode.BadRequest);
        serpapi.Calls.ShouldBe(0);
    }

    [Fact]
    public async Task SearchWithQueryLongerThanTwoHundredCharactersIsBadRequest()
    {
        var (status, _, _) = await GetAsync($"/search/web?q={s_longQuery}");

        status.ShouldBe(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task SearchReturnsMappedResults()
    {
        var ct = TestContext.Current.CancellationToken;
        var serpapi = new StubHandler(_ => StubHandler.Json(
            """{"organic_results":[{"title":"Gracery","link":"https://a.example/g","snippet":"Nice"},{"title":"Skip","link":"http://b.example/","snippet":"x"}]}"""));
        await using var factory = new SearchFetchApiFactory(serpapi, new StubHandler(_ => Page("<p>x</p>")));
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(new Uri("/search/web?q=hotel%20gracery", UriKind.Relative), ct);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
        body.GetProperty("status").GetString().ShouldBe("ok");
        var data = body.GetProperty("data");
        data.GetArrayLength().ShouldBe(1);
        data[0].GetProperty("title").GetString().ShouldBe("Gracery");
        data[0].GetProperty("url").GetString().ShouldBe("https://a.example/g");
        data[0].GetProperty("snippet").GetString().ShouldBe("Nice");
    }

    [Theory]
    [InlineData("")]
    [InlineData("?url=")]
    [InlineData("?url=%20")]
    public async Task FetchWithEmptyOrBlankUrlIsBadRequest(string query)
    {
        var (status, _, pages) = await GetAsync($"/fetch/page{query}");

        status.ShouldBe(HttpStatusCode.BadRequest);
        pages.Calls.ShouldBe(0);
    }

    [Fact]
    public async Task FetchWithUrlLongerThanTwoThousandCharactersIsBadRequest()
    {
        var (status, _, _) = await GetAsync($"/fetch/page?url={Uri.EscapeDataString(s_longUrl)}");

        status.ShouldBe(HttpStatusCode.BadRequest);
    }

    [Theory]
    [InlineData("http://example.com/")]
    [InlineData("ftp://example.com/")]
    [InlineData("not a url")]
    [InlineData("https://user:pass@example.com/")]
    public async Task FetchWithNonHttpsUrlIsAnErrorEnvelopeWithoutAnyCall(string url)
    {
        var ct = TestContext.Current.CancellationToken;
        var pages = new StubHandler(_ => Page("<p>x</p>"));
        await using var factory = new SearchFetchApiFactory(new StubHandler(_ => StubHandler.Json("{}")), pages);
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(new Uri($"/fetch/page?url={Uri.EscapeDataString(url)}", UriKind.Relative), ct);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
        body.GetProperty("status").GetString().ShouldBe("error");
        pages.Calls.ShouldBe(0);
    }

    [Fact]
    public async Task FetchReturnsTheReadablePage()
    {
        var ct = TestContext.Current.CancellationToken;
        var pages = new StubHandler(_ => Page("<title>Review</title><script>x()</script><p>Lovely rooms</p>"));
        await using var factory = new SearchFetchApiFactory(new StubHandler(_ => StubHandler.Json("{}")), pages);
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(
            new Uri($"/fetch/page?url={Uri.EscapeDataString("https://example.com/review")}", UriKind.Relative),
            ct);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
        body.GetProperty("status").GetString().ShouldBe("ok");
        var data = body.GetProperty("data");
        data.GetProperty("title").GetString().ShouldBe("Review");
        data.GetProperty("url").GetString().ShouldBe("https://example.com/review");
        var text = data.GetProperty("text").GetString().ShouldNotBeNull();
        text.ShouldNotContain("x()");
        text.ShouldContain("Lovely rooms");
        pages.Calls.ShouldBe(1);
    }

    private static HttpResponseMessage Page(string html) =>
        new(HttpStatusCode.OK) { Content = new StringContent(html, Encoding.UTF8, "text/html") };

    private static async Task<(HttpStatusCode Status, StubHandler Serpapi, StubHandler Pages)> GetAsync(string pathAndQuery)
    {
        var ct = TestContext.Current.CancellationToken;
        var serpapi = new StubHandler(_ => StubHandler.Json("""{"organic_results":[]}"""));
        var pages = new StubHandler(_ => Page("<p>x</p>"));
        await using var factory = new SearchFetchApiFactory(serpapi, pages);
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(new Uri(pathAndQuery, UriKind.Relative), ct);

        return (response.StatusCode, serpapi, pages);
    }
}
