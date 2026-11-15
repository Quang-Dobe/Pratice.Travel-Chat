using System.Net;
using System.Net.Sockets;

namespace TravelChat.Query.Features.Shared;

internal static class GuardedHandler
{
    public const int MaxRedirects = 3;

    public static readonly TimeSpan Timeout = TimeSpan.FromSeconds(10);

    internal delegate Task<IPAddress[]> Resolver(string host, CancellationToken ct);

    internal delegate ValueTask<Stream> Connector(IPAddress address, int port, CancellationToken ct);

    public static SocketsHttpHandler Create() => Create(ResolveAsync, ConnectAsync);

    public static SocketsHttpHandler Create(Resolver resolve, Connector connect) => new()
    {
        UseProxy = false,
        UseCookies = false,
        AllowAutoRedirect = true,
        MaxAutomaticRedirections = MaxRedirects,
        AutomaticDecompression = DecompressionMethods.All,
        ConnectTimeout = Timeout,
        ConnectCallback = (context, ct) => ConnectGuardedAsync(context, resolve, connect, ct),
    };

    private static async ValueTask<Stream> ConnectGuardedAsync(
        SocketsHttpConnectionContext context,
        Resolver resolve,
        Connector connect,
        CancellationToken ct)
    {
        var addresses = await resolve(context.DnsEndPoint.Host, ct);
        if (addresses.Length == 0 || addresses.Any(AddressGuard.IsBlocked))
        {
            throw new HttpRequestException("Address refused.");
        }

        SocketException? failure = null;
        foreach (var address in addresses)
        {
            try
            {
                return await connect(address, context.DnsEndPoint.Port, ct);
            }
            catch (SocketException exception)
            {
                failure = exception;
            }
        }

        throw new HttpRequestException("Connection failed.", failure);
    }

    internal static Task<IPAddress[]> ResolveAsync(string host, CancellationToken ct) =>
        IPAddress.TryParse(host, out var literal)
            ? Task.FromResult(new[] { literal })
            : Dns.GetHostAddressesAsync(host, ct);

    private static async ValueTask<Stream> ConnectAsync(IPAddress address, int port, CancellationToken ct)
    {
        var socket = new Socket(address.AddressFamily, SocketType.Stream, ProtocolType.Tcp) { NoDelay = true };
        try
        {
            await socket.ConnectAsync(new IPEndPoint(address, port), ct);
            return new NetworkStream(socket, ownsSocket: true);
        }
        catch
        {
            socket.Dispose();
            throw;
        }
    }
}
