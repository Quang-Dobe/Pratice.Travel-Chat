using System.Globalization;
using System.Text.Json;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Places;

public sealed class PlacesService(IHttpClientFactory httpClients, CachedFetch cachedFetch)
{
    public const string ClientName = "places__geoapify";

    private const string DefaultCategory = "tourism.attraction";
    private const string GeocodeUrl = "https://api.geoapify.com/v1/geocode/search";
    private const string PlacesUrl = "https://api.geoapify.com/v2/places";
    private const int RadiusMetres = 10000;
    private const int MaxResults = 20;

    private static readonly TimeSpan s_ttl = TimeSpan.FromHours(24);

    private static readonly string[] s_defaultCategories = [DefaultCategory];

    private static readonly Dictionary<string, string[]> s_interestCategories = new(StringComparer.Ordinal)
    {
        ["food"] = ["catering"],
        ["hiking"] = ["natural", "leisure"],
        ["nature"] = ["natural"],
        ["beach"] = ["beach"],
        ["museums"] = ["entertainment.museum"],
        ["museum"] = ["entertainment.museum"],
        ["art"] = ["entertainment.culture"],
        ["history"] = ["tourism.sights"],
        ["shopping"] = ["commercial.shopping_mall"],
        ["nightlife"] = ["adult.nightclub", "catering.bar"],
    };

    public static IReadOnlyList<string> CategoriesFor(IEnumerable<string> interests)
    {
        var categories = interests
            .Select(static interest => CachedFetch.Normalise(interest))
            .Where(static interest => interest.Length > 0)
            .SelectMany(static interest =>
                s_interestCategories.TryGetValue(interest, out var mapped) ? mapped : s_defaultCategories)
            .Distinct(StringComparer.Ordinal)
            .Order(StringComparer.Ordinal)
            .ToArray();

        return categories.Length == 0 ? s_defaultCategories : categories;
    }

    public async Task<ProviderResult<IReadOnlyList<Attraction>>> GetAttractionsAsync(
        string city,
        IReadOnlyList<string> interests,
        CancellationToken ct)
    {
        var categories = CategoriesFor(interests);
        var query = $"{CachedFetch.Normalise(city)}|{string.Join(',', categories)}";
        return await cachedFetch.GetAsync(
            ClientName,
            query,
            s_ttl,
            (apiKey, token) => FetchAsync(city.Trim(), categories, apiKey, token),
            ct,
            requiresKey: true);
    }

    private async Task<IReadOnlyList<Attraction>> FetchAsync(
        string city,
        IReadOnlyList<string> categories,
        string? apiKey,
        CancellationToken ct)
    {
        if (apiKey is null)
        {
            throw new ProviderFailureException(ProviderStatus.Unavailable, "no key set");
        }

        var http = httpClients.CreateClient(ClientName);
        var key = Uri.EscapeDataString(apiKey);

        var geocodeUri = new Uri(
            $"{GeocodeUrl}?text={Uri.EscapeDataString(city)}&limit=1&format=json&apiKey={key}",
            UriKind.Absolute);
        using var geocode = JsonDocument.Parse(await ResultMapping.GetBodyAsync(http, geocodeUri, ct));
        if (!geocode.RootElement.TryGetProperty("results", out var results)
            || results.ValueKind != JsonValueKind.Array
            || results.GetArrayLength() == 0)
        {
            throw new ProviderFailureException(ProviderStatus.Error, "city not found");
        }

        var place = results[0];
        var longitude = place.RequireNumber("lon").ToString(CultureInfo.InvariantCulture);
        var latitude = place.RequireNumber("lat").ToString(CultureInfo.InvariantCulture);

        var placesUri = new Uri(
            $"{PlacesUrl}?categories={Uri.EscapeDataString(string.Join(',', categories))}"
            + $"&filter=circle:{longitude},{latitude},{RadiusMetres.ToString(CultureInfo.InvariantCulture)}"
            + $"&bias=proximity:{longitude},{latitude}&limit={MaxResults.ToString(CultureInfo.InvariantCulture)}&apiKey={key}",
            UriKind.Absolute);
        using var places = JsonDocument.Parse(await ResultMapping.GetBodyAsync(http, placesUri, ct));
        var features = places.RootElement.Require("features");

        var attractions = new List<Attraction>();
        foreach (var feature in features.EnumerateArray())
        {
            var properties = feature.Require("properties");
            if (properties.OptionalString("name") is not { Length: > 0 } name)
            {
                continue;
            }

            attractions.Add(new Attraction(name, FirstCategory(properties)));
            if (attractions.Count == MaxResults)
            {
                break;
            }
        }

        return attractions;
    }

    private static string FirstCategory(JsonElement properties)
    {
        if (properties.TryGetProperty("categories", out var categories)
            && categories.ValueKind == JsonValueKind.Array
            && categories.GetArrayLength() > 0
            && categories[0].ValueKind == JsonValueKind.String
            && categories[0].GetString() is { Length: > 0 } first)
        {
            return first;
        }

        return "attraction";
    }
}
