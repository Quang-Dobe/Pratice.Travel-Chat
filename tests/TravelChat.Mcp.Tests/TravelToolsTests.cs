using System.Net;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using ModelContextProtocol.Protocol;
using ModelContextProtocol.Server;
using Shouldly;
using TravelChat.Mcp.Features.Tools;

namespace TravelChat.Mcp.Tests;

public sealed class TravelToolsTests
{
    private const string DataJson = "{\"city\":\"Lisbon\",\"days\":[{\"date\":\"2026-11-01\",\"highC\":20,\"lowC\":12,\"rainChancePct\":10}]}";

    private static readonly string[] s_expectedNames =
        ["flights__search", "hotels__search", "places__attractions", "weather__forecast"];

    [Fact]
    public void ExposesExactlyFourToolsWithConventionalNames()
    {
        var names = typeof(TravelTools)
            .GetMethods(BindingFlags.Public | BindingFlags.Instance)
            .Select(m => m.GetCustomAttribute<McpServerToolAttribute>())
            .OfType<McpServerToolAttribute>()
            .Select(a => a.Name!)
            .Order()
            .ToArray();

        names.ShouldBe(s_expectedNames);
        names.ShouldAllBe(n => Regex.IsMatch(n, "^[a-z]+__[a-z_]+$"));
    }

    [Fact]
    public async Task OkPassesDataThroughUnchanged()
    {
        var ct = TestContext.Current.CancellationToken;
        var tools = ToolsReturning(HttpStatusCode.OK, "{\"status\":\"ok\",\"message\":\"\",\"data\":" + DataJson + "}");

        var result = await tools.Forecast("Lisbon", "2026-11-01", "2026-11-02", ct);

        result.IsError.ShouldNotBe(true);
        TextOf(result).ShouldBe(DataJson);
    }

    [Theory]
    [InlineData("unavailable", "Weather data is unavailable: no key set.")]
    [InlineData("limit", "Weather data is unavailable: free-tier limit reached.")]
    [InlineData("error", "Weather data is unavailable right now.")]
    public async Task NonOkStatusMapsToErrorText(string status, string expected)
    {
        var ct = TestContext.Current.CancellationToken;
        var tools = ToolsReturning(HttpStatusCode.OK, "{\"status\":\"" + status + "\",\"message\":\"x\",\"data\":null}");

        var result = await tools.Forecast("Lisbon", "2026-11-01", "2026-11-02", ct);

        result.IsError.ShouldBe(true);
        TextOf(result).ShouldBe(expected);
    }

    [Fact]
    public async Task TransportFailureMapsToErrorText()
    {
        var ct = TestContext.Current.CancellationToken;
        var tools = ToolsWith(new FakeHandler(_ => throw new HttpRequestException("down")));

        var result = await tools.HotelsSearch("Lisbon", "2026-11-01", "2026-11-03", 2, ct);

        result.IsError.ShouldBe(true);
        TextOf(result).ShouldBe("Hotels data is unavailable right now.");
    }

    [Fact]
    public async Task ValidationProblemNamesTheInvalidField()
    {
        var ct = TestContext.Current.CancellationToken;
        var tools = ToolsReturning(
            HttpStatusCode.BadRequest,
            "{\"title\":\"One or more validation errors occurred.\",\"errors\":{\"origin\":[\"origin must be a 3-letter IATA code.\"]}}");

        var result = await tools.FlightsSearch("Lisbon", "JFK", "2026-11-01", "2026-11-05", 1, ct);

        result.IsError.ShouldBe(true);
        TextOf(result).ShouldBe(
            "Flights request was invalid: origin: origin must be a 3-letter IATA code. Fix the arguments and try again.");
    }

    [Fact]
    public async Task ServerErrorStillMapsToUnavailableRightNow()
    {
        var ct = TestContext.Current.CancellationToken;
        var tools = ToolsReturning(HttpStatusCode.InternalServerError, "{\"title\":\"boom\"}");

        var result = await tools.FlightsSearch("LIS", "JFK", "2026-11-01", "2026-11-05", 1, ct);

        result.IsError.ShouldBe(true);
        TextOf(result).ShouldBe("Flights data is unavailable right now.");
    }

    [Fact]
    public async Task AttractionsSendsInterestsCommaSeparated()
    {
        var ct = TestContext.Current.CancellationToken;
        Uri? seen = null;
        var tools = ToolsWith(new FakeHandler(request =>
        {
            seen = request.RequestUri;
            return Json(HttpStatusCode.OK, "{\"status\":\"ok\",\"message\":\"\",\"data\":[]}");
        }));

        await tools.Attractions("Lisbon", ["food", "museums"], ct);

        seen.ShouldNotBeNull();
        seen.AbsolutePath.ShouldBe("/places/attractions");
        Uri.UnescapeDataString(seen.Query).ShouldBe("?city=Lisbon&interests=food,museums");
    }

    private static TravelTools ToolsReturning(HttpStatusCode code, string body) =>
        ToolsWith(new FakeHandler(_ => Json(code, body)));

    private static TravelTools ToolsWith(HttpMessageHandler handler) =>
        new(new QueryClient(new HttpClient(handler) { BaseAddress = new Uri("http://q-query:8080") }));

    private static HttpResponseMessage Json(HttpStatusCode code, string body) =>
        new(code) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    private static string TextOf(CallToolResult result) =>
        result.Content.OfType<TextContentBlock>().Single().Text;

    private sealed class FakeHandler(Func<HttpRequestMessage, HttpResponseMessage> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(respond(request));
    }
}
