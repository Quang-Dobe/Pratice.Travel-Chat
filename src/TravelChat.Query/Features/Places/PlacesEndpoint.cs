using Microsoft.AspNetCore.Http.HttpResults;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Places;

public static class PlacesEndpoint
{
    private const int MaxCityLength = 100;
    private const int MaxInterestsLength = 200;

    public static IEndpointRouteBuilder MapPlaces(this IEndpointRouteBuilder app)
    {
        app.MapGet("/places/attractions", HandleAsync);
        return app;
    }

    private static async Task<Results<Ok<ProviderResult<IReadOnlyList<Attraction>>>, ValidationProblem>> HandleAsync(
        string? city,
        string? interests,
        PlacesService places,
        CancellationToken ct)
    {
        var errors = new QueryErrors();
        var cityText = errors.RequireText(city, "city", MaxCityLength);
        if (interests is { Length: > MaxInterestsLength })
        {
            errors.Add("interests", $"interests must be at most {MaxInterestsLength} characters.");
        }

        if (errors.Any || cityText is null)
        {
            return TypedResults.ValidationProblem(errors.Errors);
        }

        var parsed = interests?.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries) ?? [];
        var result = await places.GetAttractionsAsync(cityText, parsed, ct);
        return TypedResults.Ok(result);
    }
}
