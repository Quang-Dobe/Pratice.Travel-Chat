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

    private static async Task<QueryEnvelope> ReadInvalidAsync(HttpResponseMessage response, CancellationToken ct)
    {
        try
        {
            await using var body = await response.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(body, cancellationToken: ct);
            var root = doc.RootElement;
            var parts = new List<string>();
            if (root.ValueKind == JsonValueKind.Object
                && root.TryGetProperty("errors", out var errors)
                && errors.ValueKind == JsonValueKind.Object)
            {
                foreach (var field in errors.EnumerateObject())
                {
                    var messages = field.Value.ValueKind == JsonValueKind.Array
                        ? field.Value.EnumerateArray().Where(m => m.ValueKind == JsonValueKind.String).Select(m => m.GetString()!)
                        : [];
                    parts.Add(field.Name + ": " + string.Join(' ', messages));
                }
            }

            if (parts.Count == 0 && root.ValueKind == JsonValueKind.Object)
            {
                foreach (var name in new[] { "detail", "title" })
                {
                    if (root.TryGetProperty(name, out var text) && text.ValueKind == JsonValueKind.String)
                    {
                        parts.Add(text.GetString()!);
                        break;
                    }
                }
            }

            return parts.Count == 0
                ? QueryEnvelope.Failure()
                : new QueryEnvelope(QueryStatus.Invalid, string.Join("; ", parts), null);
        }
        catch (JsonException)
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
        catch (HttpRequestException)
        {
            return QueryEnvelope.Failure();
        }
        catch (JsonException)
        {
            return QueryEnvelope.Failure();
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            return QueryEnvelope.Failure();
        }
    }
}
