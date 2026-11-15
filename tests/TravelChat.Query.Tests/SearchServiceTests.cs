using System.Globalization;
using System.Net;
using System.Text;
using Shouldly;
using TravelChat.Query.Features.Search;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

public sealed class SearchServiceTests
{
    private static readonly Dictionary<string, string?> s_withKey = new() { ["ProviderKeys:search:serpapi"] = "secret" };

    [Fact]
    public async Task MapsTheFirstFiveHttpsResultsAndDropsTheRest()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json(EightResults()));
        var service = new SearchService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("hotel gracery reviews", ct);

        result.Status.ShouldBe(ProviderStatus.Ok);
        var items = result.Data.ShouldNotBeNull();
        items.Count.ShouldBe(5);
        string.Join(' ', items.Select(item => item.Url)).ShouldBe(
            "https://site1.example/1 https://site3.example/3 https://site4.example/4 https://site5.example/5 https://site6.example/6");
        items[0].ShouldBe(new SearchResult("Title 1", "https://site1.example/1", "Snippet 1"));
        items.ShouldAllBe(item => item.Url.StartsWith("https://", StringComparison.Ordinal));
    }

    [Fact]
    public async Task ResultWithoutTitleFallsBackToItsUrl()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json("""{"organic_results":[{"link":"https://a.example/x"}]}"""));
        var service = new SearchService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("anything", ct);

        result.Data.ShouldNotBeNull().Single().ShouldBe(new SearchResult("https://a.example/x", "https://a.example/x", string.Empty));
    }

    [Fact]
    public async Task MissingKeyIsUnavailableWithoutAnyCall()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(new Dictionary<string, string?> { ["ProviderKeys:search:serpapi"] = string.Empty });
        var handler = new StubHandler(_ => StubHandler.Json(EightResults()));
        var service = new SearchService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("ramen", ct);

        result.Status.ShouldBe(ProviderStatus.Unavailable);
        handler.Calls.ShouldBe(0);
    }

    [Fact]
    public async Task NoResultsErrorIsAnEmptyListNotAFailure()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json("""{"error":"Google hasn't returned any results for this query."}"""));
        var service = new SearchService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("zzzz", ct);

        result.Status.ShouldBe(ProviderStatus.Ok);
        result.Data.ShouldNotBeNull().ShouldBeEmpty();
    }

    [Fact]
    public async Task OtherProviderErrorIsAnError()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json("""{"error":"Invalid API key."}"""));
        var service = new SearchService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("ramen", ct);

        result.Status.ShouldBe(ProviderStatus.Error);
    }

    [Fact]
    public async Task RunOutOfSearchesIsLimit()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json("""{"error":"Your account has run out of searches."}"""));
        var service = new SearchService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("ramen", ct);

        result.Status.ShouldBe(ProviderStatus.Limit);
    }

    [Fact]
    public async Task RequestUsesTheGoogleEngineInEnglishWithTheEscapedQuery()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json("""{"organic_results":[]}"""));
        var service = new SearchService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.SearchAsync("  Ramen & Gyoza, Tokyo ", ct);

        var request = handler.Requests.Single();
        request.Host.ShouldBe("serpapi.com");
        request.Query.ShouldContain("engine=google&");
        request.Query.ShouldContain("hl=en");
        request.Query.ShouldContain("q=Ramen%20%26%20Gyoza%2C%20Tokyo&");
    }

    [Fact]
    public async Task SameNormalisedQueryIsFetchedOnce()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json(EightResults()));
        var service = new SearchService(new StubHttpClientFactory(handler), rig.Fetch);

        await service.SearchAsync("Hotel Gracery", ct);
        await service.SearchAsync("  hotel   gracery ", ct);

        handler.Calls.ShouldBe(1);
    }

    [Fact]
    public async Task TooManyRequestsIsLimit()
    {
        var ct = TestContext.Current.CancellationToken;
        using var rig = new CacheRig(s_withKey);
        var handler = new StubHandler(_ => StubHandler.Json("{}", HttpStatusCode.TooManyRequests));
        var service = new SearchService(new StubHttpClientFactory(handler), rig.Fetch);

        var result = await service.SearchAsync("ramen", ct);

        result.Status.ShouldBe(ProviderStatus.Limit);
    }

    private static string EightResults()
    {
        var builder = new StringBuilder("{\"organic_results\":[");
        for (var index = 1; index <= 8; index++)
        {
            var link = index == 2 ? "http://insecure.example/2" : $"https://site{index}.example/{index}";
            builder.Append(index > 1 ? "," : string.Empty)
                .Append(string.Create(
                    CultureInfo.InvariantCulture,
                    $"{{\"title\":\"Title {index}\",\"link\":\"{link}\",\"snippet\":\"Snippet {index}\"}}"));
        }

        return builder.Append("]}").ToString();
    }
}
