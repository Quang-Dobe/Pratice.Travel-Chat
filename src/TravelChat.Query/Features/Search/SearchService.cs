using System.Text.Json;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Search;

public sealed class SearchService(IHttpClientFactory httpClients, CachedFetch cachedFetch)
{
    public const string ClientName = "search__serpapi";

    private const string SearchUrl = "https://serpapi.com/search.json";
    private const int MaxResults = 5;
    private const int MaxTitleLength = 200;
    private const int MaxSnippetLength = 500;

    private static readonly TimeSpan s_ttl = TimeSpan.FromHours(24);

    public async Task<ProviderResult<IReadOnlyList<SearchResult>>> SearchAsync(string query, CancellationToken ct) =>
        await cachedFetch.GetWithKeyAsync(
            ClientName,
            CachedFetch.Normalise(query),
            s_ttl,
            (apiKey, token) => FetchAsync(query.Trim(), apiKey, token),
            ct);

    internal static IReadOnlyList<SearchResult> MapResults(string body)
    {
        using var document = JsonDocument.Parse(body);
        var root = document.RootElement;
        if (root.OptionalString("error") is { Length: > 0 } error)
        {
            if (IsNoResults(error))
            {
                return [];
            }

            throw new ProviderFailureException(ProviderStatus.Error, "provider returned an error");
        }

        var results = new List<SearchResult>();
        if (!root.TryGetProperty("organic_results", out var items) || items.ValueKind != JsonValueKind.Array)
        {
            return results;
        }

        foreach (var item in items.EnumerateArray())
        {
            if (SafeHttpsLink(item.OptionalString("link")) is not { } url)
            {
                continue;
            }

            var title = Truncate(item.OptionalString("title"), MaxTitleLength);
            var snippet = Truncate(item.OptionalString("snippet"), MaxSnippetLength);
            results.Add(new SearchResult(title.Length > 0 ? title : url, url, snippet));
            if (results.Count == MaxResults)
            {
                break;
            }
        }

        return results;
    }

    private static string Truncate(string? value, int maxLength)
    {
        var text = value?.Trim() ?? string.Empty;
        return text.Length > maxLength ? text[..maxLength] : text;
    }

    private static bool IsNoResults(string error) =>
        error.Contains("hasn't returned any results", StringComparison.OrdinalIgnoreCase)
        || error.Contains("no results", StringComparison.OrdinalIgnoreCase);

    private static string? SafeHttpsLink(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri) && uri.Scheme == Uri.UriSchemeHttps
            ? uri.AbsoluteUri
            : null;

    private async Task<IReadOnlyList<SearchResult>> FetchAsync(string query, string apiKey, CancellationToken ct)
    {
        var uri = new Uri(
            $"{SearchUrl}?engine=google&q={Uri.EscapeDataString(query)}&hl=en&api_key={Uri.EscapeDataString(apiKey)}",
            UriKind.Absolute);
        var body = await ResultMapping.GetBodyAsync(httpClients.CreateClient(ClientName), uri, ct);
        return MapResults(body);
    }
}
