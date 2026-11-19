using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace TravelChat.Query.Features.Shared;

internal static class SerpApi
{
    public const string SearchUrl = "https://serpapi.com/search.json";

    public static bool IsNoResults(JsonElement root)
    {
        if (root.OptionalString("error") is not { Length: > 0 } error)
        {
            return false;
        }

        if (error.Contains("hasn't returned any results", StringComparison.OrdinalIgnoreCase)
            || error.Contains("no results", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        throw new ProviderFailureException(ProviderStatus.Error, "provider returned an error");
    }

    public static string? HttpsLink(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri) && uri.Scheme == Uri.UriSchemeHttps
            ? uri.AbsoluteUri
            : null;

    public static string ShortHash(string value) =>
        Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(value)))[..12];
}
