using System.Globalization;
using System.Net;
using System.Text.Json;
using Polly;
using Polly.Timeout;

namespace TravelChat.Query.Features.Shared;

public static class ResultMapping
{
    private static readonly string[] s_quotaFields = ["error", "message", "reason"];
    private static readonly string[] s_quotaMarkers = ["quota", "limit", "run out of searches", "exceeded"];

    public static async Task<string> GetBodyAsync(HttpClient http, Uri uri, CancellationToken ct)
    {
        using var response = await http.GetAsync(uri, ct);
        return await ReadSuccessBodyAsync(response, ct);
    }

    public static async Task<string> ReadSuccessBodyAsync(HttpResponseMessage response, CancellationToken ct)
    {
        var body = await response.Content.ReadAsStringAsync(ct);
        return ToFailure(response.StatusCode, body) is { } failure ? throw failure : body;
    }

    public static ProviderFailureException? ToFailure(HttpStatusCode status, string body)
    {
        if (status == HttpStatusCode.TooManyRequests || IsQuotaBody(body))
        {
            return new ProviderFailureException(ProviderStatus.Limit, "provider quota or rate limit reached");
        }

        if ((int)status >= 400)
        {
            return new ProviderFailureException(
                ProviderStatus.Error,
                $"provider returned {((int)status).ToString(CultureInfo.InvariantCulture)}");
        }

        return null;
    }

    public static bool IsTransportFailure(Exception exception, CancellationToken ct) =>
        exception switch
        {
            HttpRequestException or JsonException or TimeoutException or ExecutionRejectedException => true,
            OperationCanceledException => !ct.IsCancellationRequested,
            _ => false,
        };

    public static ProviderFailureException ToFailure(Exception exception) =>
        exception switch
        {
            ProviderFailureException failure => failure,
            OperationCanceledException or TimeoutException or TimeoutRejectedException =>
                new ProviderFailureException(ProviderStatus.Error, "provider timed out"),
            JsonException => new ProviderFailureException(ProviderStatus.Error, "unexpected response from provider"),
            _ => new ProviderFailureException(ProviderStatus.Error, "provider unreachable"),
        };

    private static bool IsQuotaBody(string body)
    {
        if (string.IsNullOrWhiteSpace(body))
        {
            return false;
        }

        try
        {
            using var document = JsonDocument.Parse(body);
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                return false;
            }

            return s_quotaFields.Any(field => root.OptionalString(field) is { } text && ContainsQuotaMarker(text));
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private static bool ContainsQuotaMarker(string text) =>
        s_quotaMarkers.Any(marker => text.Contains(marker, StringComparison.OrdinalIgnoreCase));
}
