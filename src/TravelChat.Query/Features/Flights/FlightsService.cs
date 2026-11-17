using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Flights;

public sealed class FlightsService(IHttpClientFactory httpClients, CachedFetch cachedFetch)
{
    public const string ClientName = "flights__serpapi";

    private const string SearchUrl = "https://serpapi.com/search.json";
    private const int MaxOptions = 5;
    private const string CacheVersion = "v2";

    private static readonly TimeSpan s_ttl = TimeSpan.FromHours(6);

    private static readonly string[] s_resultGroups = ["best_flights", "other_flights"];

    // Google Flights finds nothing for IATA city codes, so each is sent as its airports.
    private static readonly Dictionary<string, string> s_cityAirports = new(StringComparer.Ordinal)
    {
        ["TYO"] = "HND,NRT",
        ["OSA"] = "KIX,ITM",
        ["SEL"] = "ICN,GMP",
        ["BJS"] = "PEK,PKX",
        ["JKT"] = "CGK,HLP",
        ["LON"] = "LHR,LGW,STN,LTN,LCY,SEN",
        ["PAR"] = "CDG,ORY",
        ["MIL"] = "MXP,LIN,BGY",
        ["ROM"] = "FCO,CIA",
        ["MOW"] = "SVO,DME,VKO",
        ["STO"] = "ARN,BMA",
        ["NYC"] = "JFK,EWR,LGA",
        ["CHI"] = "ORD,MDW",
        ["WAS"] = "IAD,DCA,BWI",
        ["YTO"] = "YYZ,YTZ",
        ["SAO"] = "GRU,CGH,VCP",
        ["RIO"] = "GIG,SDU",
        ["BUE"] = "EZE,AEP",
    };

    public async Task<ProviderResult<IReadOnlyList<FlightOption>>> SearchAsync(
        string origin,
        string destination,
        DateOnly departDate,
        DateOnly returnDate,
        int travellers,
        CancellationToken ct)
    {
        var originCode = Airports(origin);
        var destinationCode = Airports(destination);
        var query = string.Join(
            '|',
            CacheVersion,
            originCode,
            destinationCode,
            CachedFetch.Normalise(departDate),
            CachedFetch.Normalise(returnDate),
            travellers.ToString(CultureInfo.InvariantCulture));

        return await cachedFetch.GetWithKeyAsync(
            ClientName,
            query,
            s_ttl,
            (apiKey, token) => FetchAsync(originCode, destinationCode, departDate, returnDate, travellers, apiKey, token),
            ct);
    }

    private static string Airports(string code)
    {
        var upper = code.Trim().ToUpperInvariant();
        return s_cityAirports.GetValueOrDefault(upper, upper);
    }

    internal static IReadOnlyList<FlightOption> MapOptions(string body)
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

        var link = SafeHttpsLink(
            root.TryGetProperty("search_metadata", out var metadata) ? metadata.OptionalString("google_flights_url") : null);
        var options = new List<FlightOption>();
        if (link is null)
        {
            return options;
        }

        foreach (var group in s_resultGroups)
        {
            if (!root.TryGetProperty(group, out var items) || items.ValueKind != JsonValueKind.Array)
            {
                continue;
            }

            foreach (var item in items.EnumerateArray())
            {
                if (ToOption(item, link) is { } option)
                {
                    options.Add(option);
                    if (options.Count == MaxOptions)
                    {
                        return options;
                    }
                }
            }
        }

        return options;
    }

    private static FlightOption? ToOption(JsonElement item, string link)
    {
        if (!item.TryGetProperty("price", out var price) || price.ValueKind != JsonValueKind.Number)
        {
            return null;
        }

        var summary = Summarise(item);
        var token = item.OptionalString("booking_token") ?? item.OptionalString("departure_token") ?? summary;
        return new FlightOption(ShortHash(token), summary, price.GetDecimal(), link, MapLegs(item));
    }

    private static List<FlightLeg> MapLegs(JsonElement item)
    {
        if (!item.TryGetProperty("flights", out var flights)
            || flights.ValueKind != JsonValueKind.Array
            || flights.GetArrayLength() == 0)
        {
            return [];
        }

        var legs = new List<FlightLeg>();
        foreach (var flight in flights.EnumerateArray())
        {
            var departure = ReadEnd(flight, "departure_airport");
            var arrival = ReadEnd(flight, "arrival_airport");
            if (departure is null || arrival is null)
            {
                return [];
            }

            legs.Add(new FlightLeg(departure.Value.Airport, arrival.Value.Airport, departure.Value.Time, arrival.Value.Time));
        }

        return legs;
    }

    private static (Airport Airport, string Time)? ReadEnd(JsonElement flight, string property)
    {
        if (!flight.TryGetProperty(property, out var end)
            || end.OptionalString("id") is not { Length: > 0 } code
            || end.OptionalString("name") is not { Length: > 0 } name
            || end.OptionalString("time") is not { Length: > 0 } time)
        {
            return null;
        }

        return (new Airport(code, name, null, null), time);
    }

    private static string Summarise(JsonElement item)
    {
        var parts = new List<string>();
        if (item.TryGetProperty("flights", out var legs) && legs.ValueKind == JsonValueKind.Array && legs.GetArrayLength() > 0)
        {
            var first = legs[0];
            var last = legs[legs.GetArrayLength() - 1];
            if (first.OptionalString("airline") is { Length: > 0 } airline)
            {
                parts.Add(airline);
            }

            var stops = legs.GetArrayLength() - 1;
            parts.Add(stops == 0
                ? "nonstop"
                : $"{stops.ToString(CultureInfo.InvariantCulture)} stop{(stops == 1 ? string.Empty : "s")}");

            var departs = first.TryGetProperty("departure_airport", out var departure) ? departure.OptionalString("time") : null;
            var arrives = last.TryGetProperty("arrival_airport", out var arrival) ? arrival.OptionalString("time") : null;
            if (departs is not null && arrives is not null)
            {
                parts.Add($"{departs} - {arrives}");
            }
        }

        return parts.Count == 0 ? "flight option" : string.Join(", ", parts);
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

    private async Task<IReadOnlyList<FlightOption>> FetchAsync(
        string origin,
        string destination,
        DateOnly departDate,
        DateOnly returnDate,
        int travellers,
        string apiKey,
        CancellationToken ct)
    {
        var uri = new Uri(
            $"{SearchUrl}?engine=google_flights&departure_id={Uri.EscapeDataString(origin)}"
            + $"&arrival_id={Uri.EscapeDataString(destination)}"
            + $"&outbound_date={CachedFetch.Normalise(departDate)}&return_date={CachedFetch.Normalise(returnDate)}"
            + $"&type=1&currency=USD&hl=en&adults={travellers.ToString(CultureInfo.InvariantCulture)}"
            + $"&api_key={Uri.EscapeDataString(apiKey)}",
            UriKind.Absolute);

        var body = await ResultMapping.GetBodyAsync(httpClients.CreateClient(ClientName), uri, ct);
        return MapOptions(body);
    }
}
