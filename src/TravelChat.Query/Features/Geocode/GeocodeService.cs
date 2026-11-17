using System.Text.Json;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Geocode;

public sealed class GeocodeService(IHttpClientFactory httpClients, CachedFetch cachedFetch)
{
    public const string ClientName = "geocode__geoapify";

    private const string GeocodeUrl = "https://api.geoapify.com/v1/geocode/search";
    private const int MaxConcurrent = 5;

    private static readonly TimeSpan s_ttl = TimeSpan.FromDays(30);

    public async Task<ProviderResult<IReadOnlyList<GeoPoint>>> GeocodeAsync(
        IReadOnlyList<string> places,
        CancellationToken ct)
    {
        var distinct = places.Distinct(StringComparer.Ordinal).ToArray();
        using var gate = new SemaphoreSlim(MaxConcurrent);
        var lookups = distinct.Select(place => LookUpAsync(place, gate, ct)).ToArray();
        var results = await Task.WhenAll(lookups);

        if (results.Any(static result => result.Status == ProviderStatus.Unavailable))
        {
            return ProviderResult.Unavailable<IReadOnlyList<GeoPoint>>("no key set");
        }

        if (results.Any(static result => result.Status == ProviderStatus.Limit))
        {
            return ProviderResult.Limit<IReadOnlyList<GeoPoint>>("provider quota or rate limit reached");
        }

        var points = new List<GeoPoint>();
        for (var index = 0; index < distinct.Length; index++)
        {
            if (results[index].Data is { Count: > 0 } found)
            {
                points.Add(new GeoPoint(distinct[index], found[0].Lat, found[0].Lon));
            }
        }

        return ProviderResult.Ok<IReadOnlyList<GeoPoint>>(points);
    }

    private async Task<ProviderResult<IReadOnlyList<Coordinates>>> LookUpAsync(
        string place,
        SemaphoreSlim gate,
        CancellationToken ct)
    {
        await gate.WaitAsync(ct);
        try
        {
            return await cachedFetch.GetWithKeyAsync(
                ClientName,
                CachedFetch.Normalise(place),
                s_ttl,
                (apiKey, token) => FetchAsync(place.Trim(), apiKey, token),
                ct);
        }
        finally
        {
            gate.Release();
        }
    }

    private async Task<IReadOnlyList<Coordinates>> FetchAsync(string place, string apiKey, CancellationToken ct)
    {
        var uri = new Uri(
            $"{GeocodeUrl}?text={Uri.EscapeDataString(place)}&limit=1&format=json&apiKey={Uri.EscapeDataString(apiKey)}",
            UriKind.Absolute);
        var body = await ResultMapping.GetBodyAsync(httpClients.CreateClient(ClientName), uri, ct);
        return MapCoordinates(body);
    }

    internal static Coordinates[] MapCoordinates(string body)
    {
        using var document = JsonDocument.Parse(body);
        if (!document.RootElement.TryGetProperty("results", out var results)
            || results.ValueKind != JsonValueKind.Array
            || results.GetArrayLength() == 0)
        {
            return [];
        }

        var first = results[0];
        var lat = first.RequireNumber("lat");
        var lon = first.RequireNumber("lon");
        return lat is >= -90 and <= 90 && lon is >= -180 and <= 180 ? [new Coordinates(lat, lon)] : [];
    }

    internal sealed record Coordinates(double Lat, double Lon);
}
