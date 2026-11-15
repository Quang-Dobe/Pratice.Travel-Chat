using System.Text;
using Microsoft.Extensions.Options;
using TravelChat.Gateway.Features.Proxy;
using Yarp.ReverseProxy.Transforms;

namespace TravelChat.Gateway.Features.Maps;

public sealed class MapsOptions
{
    public const string SectionName = "Maps";

    public string ApiKey { get; set; } = string.Empty;
}

public static class MapsProxy
{
    public const string StylesRouteId = "maps-styles";
    public const string TilesRouteId = "maps-tiles";

    private static readonly HashSet<string> RouteIds = new(StringComparer.Ordinal)
    {
        StylesRouteId,
        TilesRouteId,
    };

    public const string UpstreamOrigin = "https://maps.geoapify.com";
    public const string PublicPrefix = "/api/maps";
    public const string KeyParameter = "apiKey";

    public static IReverseProxyBuilder AddMapsTransform(this IReverseProxyBuilder builder) =>
        builder.AddTransforms(context =>
        {
            if (RouteIds.Contains(context.Route.RouteId))
            {
                context.AddRequestTransform(ApplyRequestAsync);
                context.AddResponseTransform(ApplyResponseAsync);
            }
        });

    public static ValueTask ApplyRequestAsync(RequestTransformContext context)
    {
        var http = context.HttpContext;
        var key = http.RequestServices.GetRequiredService<IOptions<MapsOptions>>().Value.ApiKey;

        if (string.IsNullOrWhiteSpace(key))
        {
            return ApiAuthStatusCodes.WriteProblemAsync(http, StatusCodes.Status503ServiceUnavailable);
        }

        context.Query.Collection.Remove(KeyParameter);
        context.Query.Collection[KeyParameter] = key;
        return ValueTask.CompletedTask;
    }

    public static async ValueTask ApplyResponseAsync(ResponseTransformContext context)
    {
        var upstream = context.ProxyResponse;
        if (upstream is null)
        {
            return;
        }

        var http = context.HttpContext;
        var key = http.RequestServices.GetRequiredService<IOptions<MapsOptions>>().Value.ApiKey;
        var publicBase = $"{http.Request.Scheme}://{http.Request.Host.Value}{PublicPrefix}";

        var location = upstream.Headers.Location?.OriginalString;
        if (!string.IsNullOrEmpty(location))
        {
            http.Response.Headers.Location = Rewrite(location, key, publicBase);
        }

        if (!IsJson(upstream.Content.Headers.ContentType?.MediaType))
        {
            return;
        }

        context.SuppressResponseBody = true;

        if (upstream.Content.Headers.ContentEncoding.Count > 0)
        {
            http.Response.Headers.Remove("Content-Encoding");
            http.Response.Headers.Remove("Content-Length");
            http.Response.Headers.Remove("Content-Type");
            await ApiAuthStatusCodes.WriteProblemAsync(http, StatusCodes.Status502BadGateway);
            return;
        }

        var body = await upstream.Content.ReadAsStringAsync(http.RequestAborted);
        if (body.Length == 0)
        {
            return;
        }

        var bytes = Encoding.UTF8.GetBytes(Rewrite(body, key, publicBase));

        http.Response.ContentLength = bytes.Length;
        await http.Response.Body.WriteAsync(bytes, http.RequestAborted);
    }

    public static bool IsJson(string? mediaType) =>
        mediaType is not null
        && (mediaType.Equals("application/json", StringComparison.OrdinalIgnoreCase)
            || mediaType.EndsWith("+json", StringComparison.OrdinalIgnoreCase));

    public static string Rewrite(string body, string key, string publicBase)
    {
        var rewritten = body;

        if (!string.IsNullOrEmpty(key))
        {
            foreach (var form in new[] { key, Uri.EscapeDataString(key) }.Distinct(StringComparer.Ordinal))
            {
                var parameter = $"{KeyParameter}={form}";
                rewritten = rewritten
                    .Replace($"?{parameter}&", "?", StringComparison.Ordinal)
                    .Replace($"?{parameter}", string.Empty, StringComparison.Ordinal)
                    .Replace($"&{parameter}", string.Empty, StringComparison.Ordinal);
            }
        }

        return rewritten.Replace(UpstreamOrigin, publicBase, StringComparison.Ordinal);
    }
}
