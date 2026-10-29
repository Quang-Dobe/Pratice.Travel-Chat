using System.Globalization;
using Microsoft.Extensions.Caching.Hybrid;

namespace TravelChat.Query.Features.Shared;

public sealed partial class CachedFetch(HybridCache cache, ProviderKeys keys, ILogger<CachedFetch> logger)
{
    public static string Normalise(string value) =>
        string.Join(' ', value.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)).ToLowerInvariant();

    public static string Normalise(DateOnly value) =>
        value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

    public static string NormaliseList(IEnumerable<string> values) =>
        string.Join(
            ',',
            values.Select(static item => Normalise(item))
                .Where(static item => item.Length > 0)
                .Distinct(StringComparer.Ordinal)
                .Order(StringComparer.Ordinal));

    public async Task<ProviderResult<T>> GetAsync<T>(
        string client,
        string query,
        TimeSpan ttl,
        Func<string?, CancellationToken, Task<T>> fetch,
        CancellationToken ct,
        bool requiresKey = false)
    {
        var apiKey = keys.ForClient(client);
        if (requiresKey && apiKey is null)
        {
            return ProviderResult.Unavailable<T>("no key set");
        }

        var cacheKey = $"{client}:{NormaliseSegments(query)}";
        var options = new HybridCacheEntryOptions { Expiration = ttl, LocalCacheExpiration = ttl };

        try
        {
            var data = await cache.GetOrCreateAsync(
                cacheKey,
                async token =>
                {
                    LogProviderCall(logger, client);
                    return await fetch(apiKey, token);
                },
                options,
                cancellationToken: ct);
            return ProviderResult.Ok(data);
        }
        catch (ProviderFailureException failure)
        {
            return failure.ToResult<T>();
        }
        catch (Exception exception) when (ResultMapping.IsTransportFailure(exception, ct))
        {
            return ResultMapping.ToFailure(exception).ToResult<T>();
        }
    }

    private static string NormaliseSegments(string query) =>
        string.Join('|', query.Split('|').Select(static segment => Normalise(segment)));

    [LoggerMessage(Level = LogLevel.Information, Message = "Provider call {Client}")]
    private static partial void LogProviderCall(ILogger logger, string client);
}
