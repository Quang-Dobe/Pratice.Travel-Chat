using Microsoft.AspNetCore.Http.HttpResults;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Geocode;

public static class GeocodeEndpoint
{
    private const int MaxPlaces = 30;
    private const int MaxPlaceLength = 200;

    public static IEndpointRouteBuilder MapGeocode(this IEndpointRouteBuilder app)
    {
        app.MapGet("/geocode/places", HandleAsync);
        return app;
    }

    private static async Task<Results<Ok<ProviderResult<IReadOnlyList<GeoPoint>>>, ValidationProblem>> HandleAsync(
        string[]? place,
        GeocodeService geocode,
        CancellationToken ct)
    {
        var errors = new QueryErrors();
        var values = place ?? [];
        if (values.Length is < 1 or > MaxPlaces)
        {
            errors.Add("place", $"place must be given 1 to {MaxPlaces} times.");
        }
        else
        {
            foreach (var value in values)
            {
                if (errors.RequireText(value, "place", MaxPlaceLength) is null)
                {
                    break;
                }
            }
        }

        if (errors.Any)
        {
            return TypedResults.ValidationProblem(errors.Errors);
        }

        var result = await geocode.GeocodeAsync(values, ct);
        return TypedResults.Ok(result);
    }
}
