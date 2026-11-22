using System.Globalization;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Http.HttpResults;
using Microsoft.Extensions.Options;
using TravelChat.Gateway.Features.Api;

namespace TravelChat.Gateway.Features.Session;

public sealed record SessionTimeLeft(int SecondsLeft, string EndsBy);

public static class SessionEndpoints
{
    private const string EndsByIdle = "idle";
    private const string EndsByLimit = "limit";

    public static IEndpointRouteBuilder MapSessionEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet(SessionSetup.TimeLeftPath, GetTimeLeftAsync)
            .RequireAuthorization(ApiRoutes.Policy);
        return app;
    }

    private static async Task<Results<Ok<SessionTimeLeft>, ProblemHttpResult>> GetTimeLeftAsync(
        HttpContext context,
        TimeProvider clock,
        IOptions<GatewaySessionOptions> options)
    {
        var result = await context.AuthenticateAsync(CookieAuthenticationDefaults.AuthenticationScheme);
        var properties = result.Properties;
        if (!result.Succeeded
            || properties?.ExpiresUtc is not { } expiresUtc
            || !TryReadSignedInAt(properties, out var signedInAt))
        {
            return TypedResults.Problem(statusCode: StatusCodes.Status401Unauthorized);
        }

        var limitEnd = signedInAt + options.Value.AbsoluteLimit;
        var endsBy = limitEnd <= expiresUtc ? EndsByLimit : EndsByIdle;
        var end = endsBy == EndsByLimit ? limitEnd : expiresUtc;
        var secondsLeft = (int)Math.Max(0, Math.Floor((end - clock.GetUtcNow()).TotalSeconds));

        return TypedResults.Ok(new SessionTimeLeft(secondsLeft, endsBy));
    }

    private static bool TryReadSignedInAt(AuthenticationProperties properties, out DateTimeOffset signedInAt)
    {
        signedInAt = default;
        return properties.Items.TryGetValue(RedisTicketStore.SignedInAtKey, out var raw)
            && DateTimeOffset.TryParse(
                raw,
                CultureInfo.InvariantCulture,
                DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                out signedInAt);
    }
}
