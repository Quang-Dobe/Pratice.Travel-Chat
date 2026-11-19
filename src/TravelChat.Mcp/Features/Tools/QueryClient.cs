using System.Globalization;
using System.Net;
using System.Text.Json;

namespace TravelChat.Mcp.Features.Tools;

public static class QueryStatus
{
    public const string Ok = "ok";
    public const string Unavailable = "unavailable";
    public const string Limit = "limit";
    public const string Error = "error";
    public const string Invalid = "invalid";
}

public sealed record QueryEnvelope(string Status, string Message, JsonElement? Data)
{
    public static QueryEnvelope Failure() => new(QueryStatus.Error, string.Empty, null);
}

public sealed class QueryClient(HttpClient http)
{
    private static readonly JsonSerializerOptions s_json = new(JsonSerializerDefaults.Web);

    public Task<QueryEnvelope> GetForecastAsync(string city, string startDate, string endDate, CancellationToken ct) =>
        GetAsync("weather/forecast", [("city", city), ("startDate", startDate), ("endDate", endDate)], ct);

    public Task<QueryEnvelope> GetAttractionsAsync(string city, IReadOnlyList<string>? interests, CancellationToken ct) =>
        GetAsync("places/attractions", [("city", city), ("interests", string.Join(',', interests ?? []))], ct);

    public Task<QueryEnvelope> SearchFlightsAsync(
        string origin,
        string destination,
        string departDate,
        string returnDate,
        int travellers,
        CancellationToken ct) =>
        GetAsync(
            "flights/search",
            [
                ("origin", origin),
                ("destination", destination),
                ("departDate", departDate),
                ("returnDate", returnDate),
                ("travellers", travellers.ToString(CultureInfo.InvariantCulture)),
            ],
            ct);

    public Task<QueryEnvelope> SearchHotelsAsync(
        string city,
        string checkIn,
        string checkOut,
        int travellers,
        CancellationToken ct) =>
        GetAsync(
            "hotels/search",
            [
                ("city", city),
                ("checkIn", checkIn),
                ("checkOut", checkOut),
                ("travellers", travellers.ToString(CultureInfo.InvariantCulture)),
            ],
            ct);

    public Task<QueryEnvelope> GeocodeAsync(IReadOnlyList<string> places, CancellationToken ct) =>
        GetAsync("geocode/places", places.Select(p => ("place", p)), ct);

    public Task<QueryEnvelope> SearchWebAsync(string query, CancellationToken ct) =>
        GetAsync("search/web", [("q", query)], ct);

    public Task<QueryEnvelope> FetchPageAsync(string url, CancellationToken ct) =>
        GetAsync("fetch/page", [("url", url)], ct);

    private static async Task<QueryEnvelope> ReadInvalidAsync(HttpResponseMessage response, CancellationToken ct)
    {
        try
        {
            var problem = await response.Content.ReadFromJsonAsync<HttpValidationProblemDetails>(s_json, ct);
            if (problem is null)
            {
                return QueryEnvelope.Failure();
            }

            var parts = problem.Errors
                ?.Select(field => $"{field.Key}: {string.Join(' ', field.Value ?? [])}")
                .ToList() ?? [];
            if (parts.Count == 0 && (problem.Detail ?? problem.Title) is { } text)
            {
                parts = [text];
            }

            return parts.Count == 0
                ? QueryEnvelope.Failure()
                : new QueryEnvelope(QueryStatus.Invalid, string.Join("; ", parts), null);
        }
        catch (Exception exception) when (exception is JsonException or NotSupportedException)
        {
            return QueryEnvelope.Failure();
        }
    }

    private async Task<QueryEnvelope> GetAsync(string path, IEnumerable<(string Name, string Value)> query, CancellationToken ct)
    {
        var queryString = string.Join('&', query.Select(p => p.Name + "=" + Uri.EscapeDataString(p.Value)));
        var uri = new Uri(path + "?" + queryString, UriKind.Relative);
        try
        {
            using var response = await http.GetAsync(uri, ct);
            if (response.StatusCode == HttpStatusCode.BadRequest)
            {
                return await ReadInvalidAsync(response, ct);
            }

            if (!response.IsSuccessStatusCode)
            {
                return QueryEnvelope.Failure();
            }

            var envelope = await response.Content.ReadFromJsonAsync<QueryEnvelope>(s_json, ct);
            return envelope is null || string.IsNullOrEmpty(envelope.Status) ? QueryEnvelope.Failure() : envelope;
        }
        catch (Exception exception) when (exception is HttpRequestException or JsonException
            || (exception is OperationCanceledException && !ct.IsCancellationRequested))
        {
            return QueryEnvelope.Failure();
        }
    }
}
