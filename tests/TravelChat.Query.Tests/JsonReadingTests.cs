using System.Text.Json;
using Shouldly;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

public sealed class JsonReadingTests
{
    [Theory]
    [InlineData("{}")]
    [InlineData("""{"results":[]}""")]
    [InlineData("""{"results":{}}""")]
    public void FirstResultMissingEmptyOrNonArrayReturnsNull(string json)
    {
        using var document = JsonDocument.Parse(json);

        document.RootElement.FirstResult().ShouldBeNull();
    }

    [Fact]
    public void FirstResultReturnsFirstElement()
    {
        using var document = JsonDocument.Parse("""{"results":[{"id":1},{"id":2}]}""");

        document.RootElement.FirstResult()!.Value.GetProperty("id").GetInt32().ShouldBe(1);
    }

    [Theory]
    [InlineData("[]")]
    [InlineData("\"text\"")]
    public void FirstResultNonObjectRootThrowsErrorStatus(string json)
    {
        using var document = JsonDocument.Parse(json);

        var failure = Should.Throw<ProviderFailureException>(() => document.RootElement.FirstResult());

        failure.Status.ShouldBe(ProviderStatus.Error);
    }

    [Fact]
    public void LatLonValidPairReturnsIt()
    {
        using var document = JsonDocument.Parse("""{"lat":35.5,"lon":139.7}""");

        var result = document.RootElement.LatLon("lat", "lon");

        result.ShouldNotBeNull();
        result.Value.Lat.ShouldBe(35.5);
        result.Value.Lon.ShouldBe(139.7);
    }

    [Fact]
    public void LatLonBoundaryValuesAreValid()
    {
        using var document = JsonDocument.Parse("""{"lat":90,"lon":-180}""");

        var result = document.RootElement.LatLon("lat", "lon");

        result.ShouldNotBeNull();
        result.Value.Lat.ShouldBe(90);
        result.Value.Lon.ShouldBe(-180);
    }

    [Theory]
    [InlineData("""{"lat":90.1,"lon":0}""")]
    [InlineData("""{"lat":0,"lon":-180.1}""")]
    [InlineData("""{"lat":0}""")]
    [InlineData("""{"lon":0}""")]
    [InlineData("""{"lat":"1","lon":2}""")]
    [InlineData("""{"lat":1,"lon":"2"}""")]
    public void LatLonOutOfRangeMissingOrStringReturnsNull(string json)
    {
        using var document = JsonDocument.Parse(json);

        document.RootElement.LatLon("lat", "lon").ShouldBeNull();
    }

    [Fact]
    public void LatLonNonObjectReturnsNull()
    {
        using var document = JsonDocument.Parse("\"1,2\"");

        document.RootElement.LatLon("lat", "lon").ShouldBeNull();
    }
}
