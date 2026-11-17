using System.Text.Json;
using Shouldly;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

public sealed class SerpApiTests
{
    [Fact]
    public void IsNoResultsNoErrorFieldReturnsFalse()
    {
        using var document = JsonDocument.Parse("{}");

        SerpApi.IsNoResults(document.RootElement).ShouldBeFalse();
    }

    [Fact]
    public void IsNoResultsNoResultsWordingReturnsTrue()
    {
        using var document = JsonDocument.Parse("""{"error":"Google hasn't returned any results for this query."}""");

        SerpApi.IsNoResults(document.RootElement).ShouldBeTrue();
    }

    [Fact]
    public void IsNoResultsOtherErrorThrowsErrorStatus()
    {
        using var document = JsonDocument.Parse("""{"error":"Invalid API key."}""");

        var failure = Should.Throw<ProviderFailureException>(() => SerpApi.IsNoResults(document.RootElement));

        failure.Status.ShouldBe("error");
    }

    [Theory]
    [InlineData("http://a.example")]
    [InlineData("not a url")]
    [InlineData(null)]
    public void HttpsLinkHttpOrGarbageReturnsNull(string? value) =>
        SerpApi.HttpsLink(value).ShouldBeNull();

    [Fact]
    public void ShortHashIs12LowercaseHexAndStable()
    {
        var hash = SerpApi.ShortHash("token");

        hash.Length.ShouldBe(12);
        hash.ShouldMatch("^[0-9a-f]{12}$");
        SerpApi.ShortHash("token").ShouldBe(hash);
    }
}
