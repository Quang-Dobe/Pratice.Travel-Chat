using Microsoft.AspNetCore.Http.HttpResults;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Flights;

public static class FlightsEndpoint
{
    private const int IataLength = 3;
    private const int MaxTravellers = 9;

    public static IEndpointRouteBuilder MapFlights(this IEndpointRouteBuilder app)
    {
        app.MapGet("/flights/search", HandleAsync);
        return app;
    }

    private static async Task<Results<Ok<ProviderResult<IReadOnlyList<FlightOption>>>, ValidationProblem>> HandleAsync(
        string? origin,
        string? destination,
        string? departDate,
        string? returnDate,
        int? travellers,
        FlightsService flights,
        CancellationToken ct)
    {
        var errors = new QueryErrors();
        var originCode = RequireIata(errors, origin, "origin");
        var destinationCode = RequireIata(errors, destination, "destination");
        var depart = errors.RequireDate(departDate, "departDate");
        var back = errors.RequireDate(returnDate, "returnDate");
        var count = travellers ?? 1;
        if (count is < 1 or > MaxTravellers)
        {
            errors.Add("travellers", $"travellers must be between 1 and {MaxTravellers}.");
        }

        if (depart is { } d && back is { } r && r < d)
        {
            errors.Add("returnDate", "returnDate must not be before departDate.");
        }

        if (errors.Any || originCode is null || destinationCode is null || depart is null || back is null)
        {
            return TypedResults.ValidationProblem(errors.Errors);
        }

        var result = await flights.SearchAsync(originCode, destinationCode,depart.Value, back.Value, count, ct);
        return TypedResults.Ok(result);
    }

    private static string? RequireIata(QueryErrors errors, string? value, string name)
    {
        var text = errors.RequireText(value, name, IataLength);
        if (text is null)
        {
            return null;
        }

        if (text.Length != IataLength || !text.All(char.IsAsciiLetter))
        {
            errors.Add(name, $"{name} must be a 3-letter IATA code.");
            return null;
        }

        return text;
    }
}
