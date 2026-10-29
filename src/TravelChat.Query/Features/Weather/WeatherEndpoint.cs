using Microsoft.AspNetCore.Http.HttpResults;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Weather;

public static class WeatherEndpoint
{
    private const int MaxCityLength = 100;

    public static IEndpointRouteBuilder MapWeather(this IEndpointRouteBuilder app)
    {
        app.MapGet("/weather/forecast", HandleAsync);
        return app;
    }

    private static async Task<Results<Ok<ProviderResult<Forecast>>, ValidationProblem>> HandleAsync(
        string? city,
        string? startDate,
        string? endDate,
        WeatherService weather,
        CancellationToken ct)
    {
        var errors = new QueryErrors();
        var cityText = errors.RequireText(city, "city", MaxCityLength);
        var start = errors.RequireDate(startDate, "startDate");
        var end = errors.RequireDate(endDate, "endDate");
        if (start is { } startValue && end is { } endValue && endValue < startValue)
        {
            errors.Add("endDate", "endDate must not be before startDate.");
        }

        if (errors.Any || cityText is null || start is null || end is null)
        {
            return TypedResults.ValidationProblem(errors.Errors);
        }

        var result = await weather.GetForecastAsync(cityText, start.Value, end.Value, ct);
        return TypedResults.Ok(result);
    }
}
