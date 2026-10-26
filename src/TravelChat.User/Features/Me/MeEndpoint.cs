using System.Security.Claims;
using Microsoft.AspNetCore.Http.HttpResults;
using Microsoft.EntityFrameworkCore;
using TravelChat.User.Data;

namespace TravelChat.User.Features.CurrentUser;

public sealed record MeResponse(Guid Id, string Name, string Email);

public static class MeEndpoint
{
    public static IEndpointRouteBuilder MapMe(this IEndpointRouteBuilder app)
    {
        app.MapGet("/users/me", HandleAsync);
        return app;
    }

    private static async Task<Results<Ok<MeResponse>, ProblemHttpResult, UnauthorizedHttpResult>> HandleAsync(
        ClaimsPrincipal principal,
        UserDbContext db,
        CancellationToken ct)
    {
        var sub = principal.FindFirst("sub")?.Value;
        if (string.IsNullOrWhiteSpace(sub))
        {
            return TypedResults.Unauthorized();
        }

        var me = await db.Users
            .AsNoTracking()
            .Where(u => u.Oid == sub && u.IsActive)
            .Select(u => new MeResponse(u.Id, u.Name, u.Email))
            .FirstOrDefaultAsync(ct);

        return me is null
            ? TypedResults.Problem(statusCode: StatusCodes.Status404NotFound, title: "User not found")
            : TypedResults.Ok(me);
    }
}
