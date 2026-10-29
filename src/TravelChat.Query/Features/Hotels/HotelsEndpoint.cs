using Microsoft.AspNetCore.Http.HttpResults;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Hotels;

public static class HotelsEndpoint
{
    private const int MaxCityLength = 100;
    private const int MaxTravellers = 9;

    public static IEndpointRouteBuilder MapHotels(this IEndpointRouteBuilder app)
    {
        app.MapGet("/hotels/search", HandleAsync);
        return app;
    }

    private static async Task<Results<Ok<ProviderResult<IReadOnlyList<HotelOption>>>, ValidationProblem>> HandleAsync(
        string? city,
        string? checkIn,
        string? checkOut,
        int? travellers,
        HotelsService hotels,
        CancellationToken ct)
    {
        var errors = new QueryErrors();
        var cityText = errors.RequireText(city, "city", MaxCityLength);
        var checkInDate = errors.RequireDate(checkIn, "checkIn");
        var checkOutDate = errors.RequireDate(checkOut, "checkOut");
        var count = travellers ?? 1;
        if (count is < 1 or > MaxTravellers)
        {
            errors.Add("travellers", $"travellers must be between 1 and {MaxTravellers}.");
        }

        if (checkInDate is { } inDate && checkOutDate is { } outDate && outDate <= inDate)
        {
            errors.Add("checkOut", "checkOut must be after checkIn.");
        }

        if (errors.Any || cityText is null || checkInDate is null || checkOutDate is null)
        {
            return TypedResults.ValidationProblem(errors.Errors);
        }

        var result = await hotels.SearchAsync(cityText, checkInDate.Value, checkOutDate.Value, count, ct);
        return TypedResults.Ok(result);
    }
}
