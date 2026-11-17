using System.Net.Http.Headers;
using TravelChat.Gateway.Features.Api;
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
                var issuer = context.Services.GetRequiredService<TokenIssuer>();
                context.AddRequestTransform(request => ApplyAsync(request, issuer));
            }
        });

    private static ValueTask ApplyAsync(RequestTransformContext context, TokenIssuer issuer)
    {
        var http = context.HttpContext;
        var oid = http.User.FindFirst(InviteCheck.OidClaim)?.Value;
        var hasUid = Guid.TryParseExact(http.User.FindFirst(InviteCheck.UidClaim)?.Value, "D", out var uid);

        if (string.IsNullOrWhiteSpace(oid) || !hasUid || uid == Guid.Empty)
        {
            return ApiProblems.WriteAsync(http, StatusCodes.Status401Unauthorized);
        }

        var token = issuer.Mint(oid, uid);
        context.ProxyRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return ValueTask.CompletedTask;
    }
}
