using System.Text.Json.Serialization;
using Microsoft.Extensions.Options;
using TravelChat.ServiceDefaults;

namespace TravelChat.Gateway.Features.Token;

public sealed record DiscoveryDocument(
    [property: JsonPropertyName("issuer")] string Issuer,
    [property: JsonPropertyName("jwks_uri")] string JwksUri);

public sealed record JwksDocument(
    [property: JsonPropertyName("keys")] IReadOnlyList<PublicJwk> Keys);

public static class JwksEndpoints
{
    private const string JwksPath = ".well-known/jwks.json";

    public static IEndpointRouteBuilder MapJwksEndpoints(this IEndpointRouteBuilder app)
    {
        var internalPort = app.ServiceProvider
            .GetRequiredService<IOptions<InternalTokenOptions>>().Value.InternalPort;

        var group = app.MapGroup("/.well-known")
            .AllowAnonymous()
            .AddEndpointFilter((context, next) =>
                context.HttpContext.Connection.LocalPort == internalPort
                    ? next(context)
                    : ValueTask.FromResult<object?>(TypedResults.NotFound()));

        group.MapGet("/openid-configuration", (IOptions<InternalTokenOptions> options) =>
        {
            var settings = options.Value;
            return TypedResults.Json(new DiscoveryDocument(
                InternalToken.Issuer,
                new Uri(settings.InternalBaseUrl, JwksPath).AbsoluteUri));
        });

        group.MapGet("/jwks.json", (TokenIssuer issuer) =>
            TypedResults.Json(new JwksDocument([issuer.PublicJwk])));

        return app;
    }
}
