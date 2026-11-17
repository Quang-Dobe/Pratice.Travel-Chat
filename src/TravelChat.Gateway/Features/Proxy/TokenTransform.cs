using System.Net.Http.Headers;
using Microsoft.AspNetCore.Mvc;
using TravelChat.Gateway.Features.Auth;
using TravelChat.Gateway.Features.Token;
using Yarp.ReverseProxy.Transforms;

namespace TravelChat.Gateway.Features.Proxy;

public static class TokenTransform
{
    public const string UsersRouteId = "users";
    public const string ConversationsRouteId = "conversations";

    private static readonly HashSet<string> ApiRouteIds = new(StringComparer.Ordinal)
    {
        UsersRouteId,
        ConversationsRouteId,
    };

    public static IReverseProxyBuilder AddTokenTransform(this IReverseProxyBuilder builder) =>
        builder.AddTransforms(context =>
        {
            if (ApiRouteIds.Contains(context.Route.RouteId))
            {
                context.AddRequestTransform(ApplyAsync);
            }
        });

    public static ValueTask ApplyAsync(RequestTransformContext context)
    {
        var http = context.HttpContext;
        var oid = http.User.FindFirst(InviteCheck.OidClaim)?.Value;
        var hasUid = Guid.TryParseExact(http.User.FindFirst(InviteCheck.UidClaim)?.Value, "D", out var uid);

        if (string.IsNullOrWhiteSpace(oid) || !hasUid || uid == Guid.Empty)
        {
            return ApiAuthStatusCodes.WriteProblemAsync(http, StatusCodes.Status401Unauthorized);
        }

        var token = http.RequestServices.GetRequiredService<TokenIssuer>().Mint(oid, uid);
        context.ProxyRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return ValueTask.CompletedTask;
    }
}

public static class ApiAuthStatusCodes
{
    public static ValueTask WriteProblemAsync(HttpContext http, int status)
    {
        http.Response.StatusCode = status;
        return http.RequestServices.GetRequiredService<IProblemDetailsService>().WriteAsync(new ProblemDetailsContext
        {
            HttpContext = http,
            ProblemDetails = new ProblemDetails { Status = status },
        });
    }

    public static IEndpointRouteBuilder MapApiFallback(this IEndpointRouteBuilder app)
    {
        app.Map("/api/{**rest}", () => TypedResults.Problem(statusCode: StatusCodes.Status404NotFound))
            .RequireAuthorization(AuthSetup.ApiPolicy);
        return app;
    }
}
