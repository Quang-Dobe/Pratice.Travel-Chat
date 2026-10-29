using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Hotels;

public sealed class HotelsService(IHttpClientFactory httpClients, CachedFetch cachedFetch)
{
    public const string ClientName = "hotels__serpapi";

    private const string SearchUrl = "https://serpapi.com/search.json";
    private const int MaxOptions = 5;

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
            CachedFetch.Normalise(city),
            CachedFetch.Normalise(checkIn),
            CachedFetch.Normalise(checkOut),
            travellers.ToString(CultureInfo.InvariantCulture));

        return await cachedFetch.GetAsync(
            ClientName,
            query,
            s_ttl,
            (apiKey, token) => FetchAsync(city.Trim(), checkIn, checkOut, travellers, apiKey, token),
            ct,
            requiresKey: true);
    }

    internal static IReadOnlyList<HotelOption> MapOptions(string body, int nights)
    {
        using var document = JsonDocument.Parse(body);
        var root = document.RootElement;
        if (root.OptionalString("error") is { Length: > 0 } error)
        {
            if (IsNoResults(error))
            {
                return [];
            }

            throw new ProviderFailureException(ProviderStatus.Error, "provider returned an error");
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
        var link = SafeHttpsLink(property.OptionalString("link"));
        var name = property.OptionalString("name");
        var price = StayTotal(property, nights);
        if (link is null || string.IsNullOrWhiteSpace(name) || price is null)
        {
            return null;
        }

        var token = property.OptionalString("property_token") ?? link;
        return new HotelOption(ShortHash(token), Summarise(property, name), price.Value, link);
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

    private static bool IsNoResults(string error) =>
        error.Contains("hasn't returned any results", StringComparison.OrdinalIgnoreCase)
        || error.Contains("no results", StringComparison.OrdinalIgnoreCase);

    private static string? SafeHttpsLink(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri) && uri.Scheme == Uri.UriSchemeHttps
            ? uri.AbsoluteUri
            : null;

    private static string ShortHash(string value) =>
        Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(value)))[..12];

    private async Task<IReadOnlyList<HotelOption>> FetchAsync(
        string city,
        DateOnly checkIn,
        DateOnly checkOut,
        int travellers,
        string? apiKey,
        CancellationToken ct)
    {
        if (apiKey is null)
        {
            throw new ProviderFailureException(ProviderStatus.Unavailable, "no key set");
        }

        var uri = new Uri(
            $"{SearchUrl}?engine=google_hotels&q={Uri.EscapeDataString(city)}"
            + $"&check_in_date={CachedFetch.Normalise(checkIn)}&check_out_date={CachedFetch.Normalise(checkOut)}"
            + $"&adults={travellers.ToString(CultureInfo.InvariantCulture)}&currency=USD&hl=en"
            + $"&api_key={Uri.EscapeDataString(apiKey)}",
            UriKind.Absolute);

        var body = await ResultMapping.GetBodyAsync(httpClients.CreateClient(ClientName), uri, ct);
        return MapOptions(body, checkOut.DayNumber - checkIn.DayNumber);
    }
}
