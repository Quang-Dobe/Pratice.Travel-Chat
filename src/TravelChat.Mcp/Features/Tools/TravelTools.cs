using System.ComponentModel;
using ModelContextProtocol.Protocol;
using ModelContextProtocol.Server;

namespace TravelChat.Mcp.Features.Tools;

[McpServerToolType]
public sealed class TravelTools(QueryClient query)
{
    [McpServerTool(Name = "weather__forecast")]
    [Description("Daily weather forecast for a city. Dates are ISO yyyy-MM-dd. Temperatures are in degrees Celsius, rain chance is a percentage (0-100).")]
    public async Task<CallToolResult> Forecast(
        [Description("City name, e.g. Lisbon")] string city,
        [Description("First day, ISO date yyyy-MM-dd")] string startDate,
        [Description("Last day, ISO date yyyy-MM-dd, not before startDate")] string endDate,
        CancellationToken ct = default) =>
        ToResult("Weather", await query.GetForecastAsync(city, startDate, endDate, ct));

    [McpServerTool(Name = "places__attractions")]
    [Description("Up to 20 attractions in a city, each with a name and a category. Optionally narrowed by interests.")]
    public async Task<CallToolResult> Attractions(
        [Description("City name, e.g. Lisbon")] string city,
        [Description("Optional interests such as museums, food, beaches")] string[]? interests = null,
        CancellationToken ct = default) =>
        ToResult("Places", await query.GetAttractionsAsync(city, interests, ct));

    [McpServerTool(Name = "flights__search")]
    [Description("Round-trip flight options. Airports are 3-letter IATA codes (e.g. LIS, JFK). Dates are ISO yyyy-MM-dd. Prices are total prices in USD.")]
    public async Task<CallToolResult> FlightsSearch(
        [Description("Origin airport, 3-letter IATA code")] string origin,
        [Description("Destination airport, 3-letter IATA code")] string destination,
        [Description("Departure date, ISO yyyy-MM-dd")] string departDate,
        [Description("Return date, ISO yyyy-MM-dd, not before departDate")] string returnDate,
        [Description("Number of travellers, 1 to 9")] int travellers = 1,
        CancellationToken ct = default) =>
        ToResult("Flights", await query.SearchFlightsAsync(origin, destination, departDate, returnDate, travellers, ct));

    [McpServerTool(Name = "hotels__search")]
    [Description("Hotel options in a city for a stay. Dates are ISO yyyy-MM-dd. Prices are total prices in USD for the whole stay.")]
    public async Task<CallToolResult> HotelsSearch(
        [Description("City name, e.g. Lisbon")] string city,
        [Description("Check-in date, ISO yyyy-MM-dd")] string checkIn,
        [Description("Check-out date, ISO yyyy-MM-dd, after checkIn")] string checkOut,
        [Description("Number of travellers, 1 to 9")] int travellers = 1,
        CancellationToken ct = default) =>
        ToResult("Hotels", await query.SearchHotelsAsync(city, checkIn, checkOut, travellers, ct));

    internal static CallToolResult ToResult(string domain, QueryEnvelope envelope) => envelope.Status switch
    {
        QueryStatus.Ok => Text(envelope.Data?.GetRawText() ?? "null", isError: false),
        QueryStatus.Unavailable => Text(domain + " data is unavailable: no key set.", isError: true),
        QueryStatus.Limit => Text(domain + " data is unavailable: free-tier limit reached.", isError: true),
        QueryStatus.Invalid => Text(
            domain + " request was invalid: " + envelope.Message + " Fix the arguments and try again.",
            isError: true),
        _ => Text(domain + " data is unavailable right now.", isError: true),
    };

    private static CallToolResult Text(string text, bool isError) => new()
    {
        Content = [new TextContentBlock { Text = text }],
        IsError = isError,
    };
}
