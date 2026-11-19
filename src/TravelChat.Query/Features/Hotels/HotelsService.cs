using System.Globalization;
using System.Text.Json;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Hotels;

public sealed class HotelsService(IHttpClientFactory httpClients, CachedFetch cachedFetch)
{
    public const string ClientName = "hotels__serpapi";

    private const int MaxOptions = 5;
    private const string CacheVersion = "v2";

    private static readonly TimeSpan s_ttl = TimeSpan.FromHours(6);

    public async Task<ProviderResult<IReadOnlyList<HotelOption>>> SearchAsync(
        string city,
        DateOnly checkIn,
        DateOnly checkOut,
        int travellers,
        CancellationToken ct)
    {
        var query = string.Join(
            '|',
            CacheVersion,
            CachedFetch.Normalise(city),
            CachedFetch.Normalise(checkIn),
            CachedFetch.Normalise(checkOut),
            travellers.ToString(CultureInfo.InvariantCulture));

        return await cachedFetch.GetWithKeyAsync(
            ClientName,
            query,
            s_ttl,
            (apiKey, token) => FetchAsync(city.Trim(), checkIn, checkOut, travellers, apiKey, token),
            ct);
    }

    internal static IReadOnlyList<HotelOption> MapOptions(string body, int nights)
    {
        using var document = JsonDocument.Parse(body);
        var root = document.RootElement;
        if (SerpApi.IsNoResults(root))
        {
            return [];
        }

        var options = new List<HotelOption>();
        if (!root.TryGetProperty("properties", out var properties) || properties.ValueKind != JsonValueKind.Array)
        {
            return options;
        }

        foreach (var property in properties.EnumerateArray())
        {
            if (ToOption(property, nights) is { } option)
            {
                options.Add(option);
                if (options.Count == MaxOptions)
                {
                    break;
                }
            }
        }

        return options;
    }

    private static HotelOption? ToOption(JsonElement property, int nights)
    {
        var link = SerpApi.HttpsLink(property.OptionalString("link"));
        var name = property.OptionalString("name");
        var price = StayTotal(property, nights);
        if (link is null || string.IsNullOrWhiteSpace(name) || price is null)
        {
            return null;
        }

        var token = property.OptionalString("property_token") ?? link;
        return new HotelOption(SerpApi.ShortHash(token), Summarise(property, name), price.Value, link, ReadLocation(property));
    }

    private static HotelLocation? ReadLocation(JsonElement property)
    {
        return property.TryGetProperty("gps_coordinates", out var gps) && gps.LatLon("latitude", "longitude") is { } point
            ? new HotelLocation(point.Lat, point.Lon)
            : null;
    }

    private static decimal? StayTotal(JsonElement property, int nights)
    {
        if (property.TryGetProperty("total_rate", out var total) && ExtractedLowest(total) is { } stay)
        {
            return stay;
        }

        if (property.TryGetProperty("rate_per_night", out var night) && ExtractedLowest(night) is { } perNight)
        {
            return perNight * nights;
        }

        return null;
    }

    private static decimal? ExtractedLowest(JsonElement rate) =>
        rate.ValueKind == JsonValueKind.Object
        && rate.TryGetProperty("extracted_lowest", out var value)
        && value.ValueKind == JsonValueKind.Number
            ? value.GetDecimal()
            : null;

    private static string Summarise(JsonElement property, string name)
    {
        var parts = new List<string> { name.Trim() };
        if (property.OptionalString("hotel_class") is { Length: > 0 } hotelClass)
        {
            parts.Add(hotelClass);
        }

        if (property.TryGetProperty("overall_rating", out var rating) && rating.ValueKind == JsonValueKind.Number)
        {
            parts.Add($"rated {rating.GetDouble().ToString("0.0", CultureInfo.InvariantCulture)}");
        }

        return string.Join(", ", parts);
    }

    private async Task<IReadOnlyList<HotelOption>> FetchAsync(
        string city,
        DateOnly checkIn,
        DateOnly checkOut,
        int travellers,
        string apiKey,
        CancellationToken ct)
    {
        var uri = new Uri(
            $"{SerpApi.SearchUrl}?engine=google_hotels&q={Uri.EscapeDataString(city)}"
            + $"&check_in_date={CachedFetch.Normalise(checkIn)}&check_out_date={CachedFetch.Normalise(checkOut)}"
            + $"&adults={travellers.ToString(CultureInfo.InvariantCulture)}&currency=USD&hl=en"
            + $"&api_key={Uri.EscapeDataString(apiKey)}",
            UriKind.Absolute);

        var body = await ResultMapping.GetBodyAsync(httpClients.CreateClient(ClientName), uri, ct);
        return MapOptions(body, checkOut.DayNumber - checkIn.DayNumber);
    }
}
