using System.Globalization;
using System.Text.Json;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Weather;

public sealed class WeatherService(IHttpClientFactory httpClients, CachedFetch cachedFetch, TimeProvider clock)
{
    public const string ClientName = "weather__open_meteo";

    private const int HorizonDays = 15;
    private const string GeocodingUrl = "https://geocoding-api.open-meteo.com/v1/search";
    private const string ForecastUrl = "https://api.open-meteo.com/v1/forecast";
    private const string DailyFields = "temperature_2m_max,temperature_2m_min,precipitation_probability_max";

    private static readonly TimeSpan s_ttl = TimeSpan.FromHours(1);

    public async Task<ProviderResult<Forecast>> GetForecastAsync(
        string city,
        DateOnly startDate,
        DateOnly endDate,
        CancellationToken ct)
    {
        var today = DateOnly.FromDateTime(clock.GetUtcNow().UtcDateTime);
        var first = startDate > today ? startDate : today;
        var horizon = today.AddDays(HorizonDays);
        var last = endDate < horizon ? endDate : horizon;
        var requestedCity = city.Trim();

        if (first > last)
        {
            return ProviderResult.Ok(new Forecast(requestedCity, []));
        }

        var query = $"{CachedFetch.Normalise(city)}|{CachedFetch.Normalise(first)}|{CachedFetch.Normalise(last)}";
        return await cachedFetch.GetAsync(
            ClientName,
            query,
            s_ttl,
            (_, token) => FetchAsync(requestedCity, first, last, token),
            ct);
    }

    private async Task<Forecast> FetchAsync(string city, DateOnly first, DateOnly last, CancellationToken ct)
    {
        var http = httpClients.CreateClient(ClientName);

        var geocodeUri = new Uri(
            $"{GeocodingUrl}?name={Uri.EscapeDataString(city)}&count=1&language=en&format=json",
            UriKind.Absolute);
        using var geocode = JsonDocument.Parse(await ResultMapping.GetBodyAsync(http, geocodeUri, ct));
        if (!geocode.RootElement.TryGetProperty("results", out var results)
            || results.ValueKind != JsonValueKind.Array
            || results.GetArrayLength() == 0)
        {
            throw new ProviderFailureException(ProviderStatus.Error, "city not found");
        }

        var place = results[0];
        var latitude = place.RequireNumber("latitude").ToString(CultureInfo.InvariantCulture);
        var longitude = place.RequireNumber("longitude").ToString(CultureInfo.InvariantCulture);
        var resolvedCity = place.OptionalString("name") is { Length: > 0 } name ? name : city;

        var forecastUri = new Uri(
            $"{ForecastUrl}?latitude={latitude}&longitude={longitude}&daily={DailyFields}&timezone=auto"
            + $"&start_date={CachedFetch.Normalise(first)}&end_date={CachedFetch.Normalise(last)}",
            UriKind.Absolute);
        using var forecast = JsonDocument.Parse(await ResultMapping.GetBodyAsync(http, forecastUri, ct));
        var daily = forecast.RootElement.Require("daily");
        var times = daily.Require("time");
        var highs = daily.Require("temperature_2m_max");
        var lows = daily.Require("temperature_2m_min");
        var rain = daily.Require("precipitation_probability_max");

        var days = new List<ForecastDay>();
        var index = 0;
        foreach (var time in times.EnumerateArray())
        {
            int? rainChance = null;
            if (rain.NumberAt(index) is { } chance)
            {
                rainChance = (int)Math.Round(chance);
            }

            days.Add(new ForecastDay(time.GetString() ?? string.Empty, highs.NumberAt(index), lows.NumberAt(index), rainChance));
            index++;
        }

        return new Forecast(resolvedCity, days);
    }
}
