using System.Net;
using System.Net.Sockets;
using System.Text;
using Microsoft.Extensions.DependencyInjection;
using Shouldly;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

internal sealed class ScriptedStream(byte[] response) : Stream
{
    private int _position;

    public override bool CanRead => true;

    public override bool CanSeek => false;

    public override bool CanWrite => true;

    public override long Length => throw new NotSupportedException();

    public override long Position
    {
        get => throw new NotSupportedException();
        set => throw new NotSupportedException();
    }

    public override void Flush()
    {
    }

    public override Task FlushAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    public override int Read(byte[] buffer, int offset, int count) => Read(buffer.AsSpan(offset, count));

    public override int Read(Span<byte> buffer)
    {
        var count = Math.Min(buffer.Length, response.Length - _position);
        response.AsSpan(_position, count).CopyTo(buffer);
        _position += count;
        return count;
    }

    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default) =>
        ValueTask.FromResult(Read(buffer.Span));

    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken) =>
        Task.FromResult(Read(buffer.AsSpan(offset, count)));

    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

    public override void SetLength(long value) => throw new NotSupportedException();

    public override void Write(byte[] buffer, int offset, int count)
    {
    }

    public override void Write(ReadOnlySpan<byte> buffer)
    {
    }

    public override Task WriteAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken) =>
        Task.CompletedTask;

    public override ValueTask WriteAsync(ReadOnlyMemory<byte> buffer, CancellationToken cancellationToken = default) =>
        ValueTask.CompletedTask;
}

internal sealed class FakeNetwork
{
    private readonly Dictionary<string, IPAddress> _hosts = new(StringComparer.Ordinal);
    private readonly Dictionary<IPAddress, string> _responses = [];
    private readonly List<string> _connected = [];

    public IReadOnlyList<string> Connected
    {
        get
        {
            lock (_connected)
            {
                return [.. _connected];
            }
        }
    }

    public static string Redirect(string location) =>
        $"HTTP/1.1 302 Found\r\nLocation: {location}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";

    public static string Ok(string body) =>
        $"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {body.Length}\r\nConnection: close\r\n\r\n{body}";

    public FakeNetwork Host(string host, string ip, string response)
    {
        var address = IPAddress.Parse(ip);
        _hosts[host] = address;
        _responses[address] = response;
        return this;
    }

    public HttpClient Client() => new(GuardedHandler.Create(ResolveAsync, ConnectAsync));

    private Task<IPAddress[]> ResolveAsync(string host, CancellationToken ct) =>
        Task.FromResult(new[] { _hosts[host] });

    private ValueTask<Stream> ConnectAsync(IPAddress address, int port, CancellationToken ct)
    {
        lock (_connected)
        {
            _connected.Add(address.ToString());
        }

        return ValueTask.FromResult<Stream>(new ScriptedStream(Encoding.ASCII.GetBytes(_responses[address])));
    }
}

public sealed class GuardedHandlerTests
{
    [Theory]
    [InlineData("127.0.0.1")]
    [InlineData("127.255.255.254")]
    [InlineData("0.0.0.0")]
    [InlineData("10.0.0.1")]
    [InlineData("10.255.255.255")]
    [InlineData("172.16.0.1")]
    [InlineData("172.31.255.255")]
    [InlineData("192.168.0.1")]
    [InlineData("192.168.255.255")]
    [InlineData("169.254.169.254")]
    [InlineData("100.64.0.1")]
    [InlineData("224.0.0.1")]
    [InlineData("255.255.255.255")]
    [InlineData("::1")]
    [InlineData("::")]
    [InlineData("fe80::1")]
    [InlineData("febf::1")]
    [InlineData("fc00::1")]
    [InlineData("fd12:3456::1")]
    [InlineData("ff02::1")]
    [InlineData("::ffff:127.0.0.1")]
    [InlineData("::ffff:10.0.0.1")]
    [InlineData("::ffff:172.16.5.5")]
    [InlineData("::ffff:192.168.1.1")]
    [InlineData("::ffff:169.254.169.254")]
    [InlineData("::ffff:0.0.0.0")]
    [InlineData("::10.0.0.1")]
    [InlineData("64:ff9b::7f00:1")]
    [InlineData("64:ff9b::a9fe:a9fe")]
    [InlineData("2002:7f00:1::")]
    [InlineData("2002:0a00:0001::")]
    public void RefusesEveryPrivateClass(string address)
    {
        AddressGuard.IsBlocked(IPAddress.Parse(address)).ShouldBeTrue();
    }

    [Theory]
    [InlineData("93.184.216.34")]
    [InlineData("8.8.8.8")]
    [InlineData("172.15.255.255")]
    [InlineData("172.32.0.1")]
    [InlineData("192.169.0.1")]
    [InlineData("100.63.255.255")]
    [InlineData("2606:4700:4700::1111")]
    [InlineData("2a00:1450:4001::200e")]
    [InlineData("::ffff:8.8.8.8")]
    [InlineData("64:ff9b::808:808")]
    public void AllowsPublicAddresses(string address)
    {
        AddressGuard.IsBlocked(IPAddress.Parse(address)).ShouldBeFalse();
    }

    [Theory]
    [InlineData("http://127.0.0.1/")]
    [InlineData("http://0.0.0.0/")]
    [InlineData("http://10.1.2.3:8080/")]
    [InlineData("http://172.16.0.9/")]
    [InlineData("http://192.168.1.1/")]
    [InlineData("http://169.254.169.254/latest/meta-data")]
    [InlineData("http://[::1]/")]
    [InlineData("http://[fe80::1]/")]
    [InlineData("http://[fd00::1]/")]
    [InlineData("http://[::ffff:127.0.0.1]/")]
    [InlineData("http://[::ffff:10.0.0.1]/")]
    public async Task AddressLiteralsAreRefusedBeforeAnyConnection(string url)
    {
        var ct = TestContext.Current.CancellationToken;
        var connections = 0;
        using var http = new HttpClient(GuardedHandler.Create(
            GuardedHandler.ResolveAsync,
            (_, _, _) =>
            {
                Interlocked.Increment(ref connections);
                throw new SocketException();
            }));

        await Should.ThrowAsync<HttpRequestException>(() => http.GetAsync(new Uri(url), ct));

        connections.ShouldBe(0);
    }

    [Fact]
    public async Task NameResolvingToPrivateAddressIsRefused()
    {
        var ct = TestContext.Current.CancellationToken;
        var network = new FakeNetwork().Host("internal.test", "10.0.0.5", FakeNetwork.Ok("secret"));
        using var http = network.Client();

        await Should.ThrowAsync<HttpRequestException>(() => http.GetAsync(new Uri("http://internal.test/"), ct));

        network.Connected.ShouldBeEmpty();
    }

    [Fact]
    public async Task NameResolvingToAnyPrivateAddressAmongPublicOnesIsRefused()
    {
        var ct = TestContext.Current.CancellationToken;
        var connections = 0;
        using var http = new HttpClient(GuardedHandler.Create(
            (_, _) => Task.FromResult(new[] { IPAddress.Parse("93.184.216.34"), IPAddress.Parse("10.0.0.5") }),
            (_, _, _) =>
            {
                Interlocked.Increment(ref connections);
                throw new SocketException();
            }));

        await Should.ThrowAsync<HttpRequestException>(() => http.GetAsync(new Uri("http://mixed.test/"), ct));

        connections.ShouldBe(0);
    }

    [Fact]
    public async Task NameResolvingToNothingIsRefused()
    {
        var ct = TestContext.Current.CancellationToken;
        using var http = new HttpClient(GuardedHandler.Create(
            (_, _) => Task.FromResult(Array.Empty<IPAddress>()),
            (_, _, _) => throw new SocketException()));

        await Should.ThrowAsync<HttpRequestException>(() => http.GetAsync(new Uri("http://empty.test/"), ct));
    }

    [Fact]
    public async Task PublicAddressIsConnectedAndServed()
    {
        var ct = TestContext.Current.CancellationToken;
        var network = new FakeNetwork().Host("public.test", "93.184.216.34", FakeNetwork.Ok("fine"));
        using var http = network.Client();

        using var response = await http.GetAsync(new Uri("http://public.test/"), ct);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        (await response.Content.ReadAsStringAsync(ct)).ShouldBe("fine");
        string.Join(',', network.Connected).ShouldBe("93.184.216.34");
    }

    [Fact]
    public async Task RedirectToPrivateAddressIsRefusedOnTheSecondHop()
    {
        var ct = TestContext.Current.CancellationToken;
        var network = new FakeNetwork()
            .Host("public.test", "93.184.216.34", FakeNetwork.Redirect("http://internal.test/"))
            .Host("internal.test", "10.0.0.5", FakeNetwork.Ok("secret"));
        using var http = network.Client();

        await Should.ThrowAsync<HttpRequestException>(() => http.GetAsync(new Uri("http://public.test/"), ct));

        string.Join(',', network.Connected).ShouldBe("93.184.216.34");
    }

    [Fact]
    public async Task RedirectToCloudMetadataAddressLiteralIsRefused()
    {
        var ct = TestContext.Current.CancellationToken;
        var connections = new List<string>();
        using var http = new HttpClient(GuardedHandler.Create(
            (host, token) => host == "public.test"
                ? Task.FromResult(new[] { IPAddress.Parse("93.184.216.34") })
                : GuardedHandler.ResolveAsync(host, token),
            (address, _, _) =>
            {
                connections.Add(address.ToString());
                return ValueTask.FromResult<Stream>(
                    new ScriptedStream(Encoding.ASCII.GetBytes(FakeNetwork.Redirect("http://169.254.169.254/latest"))));
            }));

        await Should.ThrowAsync<HttpRequestException>(() => http.GetAsync(new Uri("http://public.test/"), ct));

        string.Join(',', connections).ShouldBe("93.184.216.34");
    }

    [Fact]
    public async Task AFourthRedirectIsNotFollowed()
    {
        var ct = TestContext.Current.CancellationToken;
        var network = new FakeNetwork()
            .Host("h1.test", "93.184.216.1", FakeNetwork.Redirect("http://h2.test/"))
            .Host("h2.test", "93.184.216.2", FakeNetwork.Redirect("http://h3.test/"))
            .Host("h3.test", "93.184.216.3", FakeNetwork.Redirect("http://h4.test/"))
            .Host("h4.test", "93.184.216.4", FakeNetwork.Redirect("http://h5.test/"))
            .Host("h5.test", "93.184.216.5", FakeNetwork.Ok("too far"));
        using var http = network.Client();

        using var response = await http.GetAsync(new Uri("http://h1.test/"), ct);

        response.StatusCode.ShouldBe(HttpStatusCode.Found);
        string.Join(',', network.Connected).ShouldBe("93.184.216.1,93.184.216.2,93.184.216.3,93.184.216.4");
    }

    [Fact]
    public async Task ThreeRedirectsAreFollowed()
    {
        var ct = TestContext.Current.CancellationToken;
        var network = new FakeNetwork()
            .Host("h1.test", "93.184.216.1", FakeNetwork.Redirect("http://h2.test/"))
            .Host("h2.test", "93.184.216.2", FakeNetwork.Redirect("http://h3.test/"))
            .Host("h3.test", "93.184.216.3", FakeNetwork.Redirect("http://h4.test/"))
            .Host("h4.test", "93.184.216.4", FakeNetwork.Ok("arrived"));
        using var http = network.Client();

        using var response = await http.GetAsync(new Uri("http://h1.test/"), ct);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        (await response.Content.ReadAsStringAsync(ct)).ShouldBe("arrived");
    }

    [Fact]
    public void GuardedHandlerHasCookiesOff()
    {
        using var handler = GuardedHandler.Create();

        handler.UseCookies.ShouldBeFalse();
    }

    [Fact]
    public void GuardedClientHasATenSecondTimeout()
    {
        var services = new ServiceCollection();
        services.AddGuardedClient("guarded__test");
        using var provider = services.BuildServiceProvider();

        using var http = provider.GetRequiredService<IHttpClientFactory>().CreateClient("guarded__test");

        http.Timeout.ShouldBe(TimeSpan.FromSeconds(10));
    }
}
