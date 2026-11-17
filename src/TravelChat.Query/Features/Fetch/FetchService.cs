using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Fetch;

public sealed partial class FetchService(IHttpClientFactory httpClients, CachedFetch cachedFetch)
{
    public const string ClientName = "fetch__web";

    private const string Unreadable = "page could not be read";
    private const int MaxBytes = 1024 * 1024;
    private const int MaxTextLength = 6000;
    private const int MaxTitleLength = 200;
    private const int RegexTimeoutMilliseconds = 1000;

    private static readonly TimeSpan s_ttl = TimeSpan.FromHours(24);

    public static Uri? ParseHttps(string? value) =>
        Uri.TryCreate(value?.Trim(), UriKind.Absolute, out var uri)
        && uri.Scheme == Uri.UriSchemeHttps
        && uri.UserInfo.Length == 0
            ? uri
            : null;

    public async Task<ProviderResult<PageText>> GetPageAsync(Uri url, CancellationToken ct)
    {
        if (!url.IsAbsoluteUri || url.Scheme != Uri.UriSchemeHttps || url.UserInfo.Length > 0)
        {
            return ProviderResult.Error<PageText>(Unreadable);
        }

        return await cachedFetch.GetAsync(
            ClientName,
            CacheKey(url),
            s_ttl,
            token => FetchAsync(url, token),
            ct);
    }

    internal static PageText MapPage(Uri url, string body, bool isHtml)
    {
        try
        {
            if (!isHtml)
            {
                return new PageText(string.Empty, url.AbsoluteUri, Clean(body, MaxTextLength, decode: false));
            }

            var title = TitleRegex().Match(body) is { Success: true } match
                ? Clean(match.Groups[1].Value, MaxTitleLength)
                : string.Empty;
            var stripped = CommentRegex().Replace(body, " ");
            stripped = BlockRegex().Replace(stripped, " ");
            stripped = UnclosedBlockRegex().Replace(stripped, " ");
            stripped = TagRegex().Replace(stripped, " ");
            return new PageText(title, url.AbsoluteUri, Clean(stripped, MaxTextLength));
        }
        catch (RegexMatchTimeoutException)
        {
            throw new ProviderFailureException(ProviderStatus.Error, Unreadable);
        }
    }

    [GeneratedRegex(@"<title\b[^>]*>(.*?)</title\s*>", RegexOptions.IgnoreCase | RegexOptions.Singleline, RegexTimeoutMilliseconds)]
    private static partial Regex TitleRegex();

    [GeneratedRegex(@"<!--.*?-->", RegexOptions.Singleline, RegexTimeoutMilliseconds)]
    private static partial Regex CommentRegex();

    [GeneratedRegex(@"<(script|style)\b[^>]*>.*?</\1\s*>", RegexOptions.IgnoreCase | RegexOptions.Singleline, RegexTimeoutMilliseconds)]
    private static partial Regex BlockRegex();

    [GeneratedRegex(@"<(?:script|style)\b.*$", RegexOptions.IgnoreCase | RegexOptions.Singleline, RegexTimeoutMilliseconds)]
    private static partial Regex UnclosedBlockRegex();

    [GeneratedRegex(@"<[a-zA-Z/!?][^>]*>?", RegexOptions.None, RegexTimeoutMilliseconds)]
    private static partial Regex TagRegex();

    [GeneratedRegex(@"\s+", RegexOptions.None, RegexTimeoutMilliseconds)]
    private static partial Regex WhitespaceRegex();

    private static string Clean(string value, int maxLength, bool decode = true)
    {
        var text = WhitespaceRegex().Replace(decode ? WebUtility.HtmlDecode(value) : value, " ").Trim();
        if (text.Length <= maxLength)
        {
            return text;
        }

        var cut = char.IsHighSurrogate(text[maxLength - 1]) ? maxLength - 1 : maxLength;
        return text[..cut];
    }

    private static string CacheKey(Uri url) =>
        Convert.ToHexStringLower(
            SHA256.HashData(Encoding.UTF8.GetBytes(url.GetComponents(UriComponents.HttpRequestUrl, UriFormat.UriEscaped))));

    private static ProviderFailureException Failure() => new(ProviderStatus.Error, Unreadable);

    private static Encoding ResolveEncoding(string? charset)
    {
        if (string.IsNullOrWhiteSpace(charset))
        {
            return Encoding.UTF8;
        }

        try
        {
            return Encoding.GetEncoding(charset.Trim('"', '\''));
        }
        catch (ArgumentException)
        {
            return Encoding.UTF8;
        }
    }

    private static async Task<byte[]> ReadCappedAsync(Stream stream, CancellationToken ct)
    {
        var buffer = new byte[MaxBytes];
        var total = 0;
        while (total < MaxBytes)
        {
            var read = await stream.ReadAsync(buffer.AsMemory(total, MaxBytes - total), ct);
            if (read == 0)
            {
                break;
            }

            total += read;
        }

        return buffer[..total];
    }

    private async Task<PageText> FetchAsync(Uri url, CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(GuardedHandler.Timeout);
        try
        {
            using var response = await httpClients.CreateClient(ClientName)
                .GetAsync(url, HttpCompletionOption.ResponseHeadersRead, timeout.Token);
            if (!response.IsSuccessStatusCode
                || response.RequestMessage?.RequestUri is { Scheme: not "https" })
            {
                throw Failure();
            }

            var contentType = response.Content.Headers.ContentType;
            var isHtml = string.Equals(contentType?.MediaType, "text/html", StringComparison.OrdinalIgnoreCase);
            if (!isHtml && !string.Equals(contentType?.MediaType, "text/plain", StringComparison.OrdinalIgnoreCase))
            {
                throw Failure();
            }

            await using var stream = await response.Content.ReadAsStreamAsync(timeout.Token);
            var bytes = await ReadCappedAsync(stream, timeout.Token);
            var body = ResolveEncoding(contentType?.CharSet).GetString(bytes);
            return MapPage(url, body, isHtml);
        }
        catch (Exception exception) when (
            exception is HttpRequestException or IOException
            || (exception is OperationCanceledException && !ct.IsCancellationRequested))
        {
            throw Failure();
        }
    }
}
