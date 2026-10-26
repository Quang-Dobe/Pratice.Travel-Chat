using System.IO.Pipelines;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Claims;
using System.Text;
using Microsoft.AspNetCore.Authentication.OpenIdConnect;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.IdentityModel.Protocols;
using Microsoft.IdentityModel.Protocols.OpenIdConnect;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.IdentityModel.JsonWebTokens;
using Shouldly;

namespace TravelChat.Gateway.Tests;

public sealed class RouteAuthGuardTests : IClassFixture<GatewayFactory>
{
    private const string UsersPattern = "/api/users/{**rest}";
    private const string ConversationsPattern = "/api/conversations/{**rest}";
    private const string WebPattern = "/{**catch-all}";

    private readonly GatewayFactory _factory;

    public RouteAuthGuardTests(GatewayFactory factory)
    {
        _factory = factory;
        _factory.Destinations.Reset();
    }

    private static HttpRequestMessage NewRequest(HttpMethod method, string path, string? cookie = null)
    {
        var request = new HttpRequestMessage(method, new Uri(path, UriKind.Relative));
        if (cookie is not null)
        {
            request.Headers.Add("Cookie", cookie);
        }

        return request;
    }

    private Task<string> SessionCookie(string? oid, string? uid) => _factory.CreateSessionCookieAsync(oid, uid);

    [Fact]
    public async Task SessionCookieAuthenticatesThroughTheRealCookieHandler()
    {
        var uid = Guid.NewGuid().ToString("D");
        var cookie = await SessionCookie("oid-1", uid);
        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(HttpMethod.Get, GatewayFactory.TestEndpointsStartupFilter.WhoAmIPath, cookie);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        var body = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        response.StatusCode.ShouldBe(HttpStatusCode.OK, body);
        body.ShouldContain("oid=oid-1");
        body.ShouldContain($"uid={uid}");
    }

    private static JsonWebToken ReadBearer(SeenRequest seen)
    {
        var authorization = seen.Authorization.ShouldNotBeNull();
        authorization.Scheme.ShouldBe("Bearer");
        return new JsonWebTokenHandler().ReadJsonWebToken(authorization.Parameter.ShouldNotBeNull());
    }

    private static IEnumerable<RouteEndpoint> RouteEndpoints(GatewayFactory factory) =>
        factory.Services
            .GetServices<EndpointDataSource>()
            .SelectMany(source => source.Endpoints)
            .Distinct()
            .OfType<RouteEndpoint>();

    private static bool IsAnonymous(Endpoint endpoint) =>
        endpoint.Metadata.GetMetadata<IAllowAnonymous>() is not null;

    [Fact]
    public void AnonymousEndpointsAreExactlyTheIntendedOnes()
    {
        var anonymous = RouteEndpoints(_factory)
            .Where(IsAnonymous)
            .Select(endpoint => endpoint.RoutePattern.RawText ?? string.Empty)
            .Distinct()
            .Order()
            .ToList();

        string[] expected =
        [
            "/.well-known/jwks.json",
            "/.well-known/openid-configuration",
            "/auth/login",
            "/auth/logout",
            "/health/live",
            WebPattern,
        ];

        anonymous.ShouldBe(expected.Order().ToList());
    }

    [Theory]
    [InlineData(UsersPattern)]
    [InlineData(ConversationsPattern)]
    public void ApiRoutesExistAndRequireAuthorization(string pattern)
    {
        var endpoint = RouteEndpoints(_factory)
            .Single(candidate => candidate.RoutePattern.RawText == pattern);

        IsAnonymous(endpoint).ShouldBeFalse();
    }

    [Theory]
    [InlineData("GET", "/api/users/me")]
    [InlineData("GET", "/api/conversations")]
    [InlineData("POST", "/api/conversations/abc/messages")]
    public async Task ApiWithoutSessionReturns401NotRedirect(string method, string path)
    {
        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(new HttpMethod(method), path);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.Unauthorized);
        response.Headers.Location.ShouldBeNull();
        _factory.Destinations.Requests.ShouldBeEmpty();
    }

    [Theory]
    [InlineData("/api")]
    [InlineData("/api/nope")]
    [InlineData("/api/users/me")]
    public async Task ApiWithoutSessionReturnsProblemBody(string path)
    {
        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(HttpMethod.Get, path);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.Unauthorized);
        var body = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        body.ShouldContain("401");
        _factory.Destinations.Requests.ShouldBeEmpty();
    }

    [Theory]
    [InlineData("/api")]
    [InlineData("/api/nope")]
    public async Task UnknownApiPathWithSessionReturns404AndNeverReachesWeb(string path)
    {
        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(
            HttpMethod.Get,
            path,
            await SessionCookie("oid-1", Guid.NewGuid().ToString("D")));

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.NotFound);
        _factory.Destinations.Requests.ShouldBeEmpty();
    }

    [Fact]
    public async Task ApiWithoutSessionReturns401EvenWhenIdentityProviderMetadataIsUnreachable()
    {
        using var broken = _factory.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
            services.PostConfigure<OpenIdConnectOptions>(OpenIdConnectDefaults.AuthenticationScheme, options =>
            {
                options.Configuration = null;
                options.ConfigurationManager = new UnreachableConfigurationManager();
            })));
        using var client = broken.CreateClient(
            new WebApplicationFactoryClientOptions { AllowAutoRedirect = false, HandleCookies = false });
        using var request = NewRequest(HttpMethod.Get, "/api/users/me");

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.Unauthorized);
    }

    private sealed class UnreachableConfigurationManager : IConfigurationManager<OpenIdConnectConfiguration>
    {
        public Task<OpenIdConnectConfiguration> GetConfigurationAsync(CancellationToken cancel) =>
            throw new InvalidOperationException("IDX20803: metadata unreachable");

        public void RequestRefresh()
        {
        }
    }

    [Fact]
    public async Task NonApiChallengeStillRedirectsToIdentityProvider()
    {
        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(HttpMethod.Get, "/auth/login");

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.Redirect);
        response.Headers.Location.ShouldNotBeNull().AbsoluteUri
            .ShouldStartWith(GatewayFactory.IdentityProviderAuthorizeEndpoint);
        _factory.Destinations.Requests.ShouldBeEmpty();
    }

    [Theory]
    [InlineData("/api/users/me", "q-user", 8080, "/users/me")]
    [InlineData("/api/conversations", "q-agent", 8000, "/conversations")]
    [InlineData("/api/conversations/abc/messages", "q-agent", 8000, "/conversations/abc/messages")]
    public async Task ApiForwardsWithBearerAndWithoutCookie(string path, string host, int port, string forwardedPath)
    {
        var oid = "oid-1";
        var uid = Guid.NewGuid();
        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(HttpMethod.Get, path, await SessionCookie(oid, uid.ToString("D")));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", "client-supplied");

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var seen = _factory.Destinations.Requests.ShouldHaveSingleItem();
        seen.Uri.Host.ShouldBe(host);
        seen.Uri.Port.ShouldBe(port);
        seen.Uri.AbsolutePath.ShouldBe(forwardedPath);
        seen.HasCookie.ShouldBeFalse();

        var jwt = ReadBearer(seen);
        jwt.Subject.ShouldBe(oid);
        jwt.GetClaim("uid").Value.ShouldBe(uid.ToString("D"));
        jwt.Issuer.ShouldBe("q-gateway");
        jwt.Audiences.ShouldContain("q-internal");
    }

    [Fact]
    public async Task EachSessionForwardsItsOwnUid()
    {
        var uidA = Guid.NewGuid();
        var uidB = Guid.NewGuid();
        using var client = _factory.CreateGatewayClient();

        foreach (var (oid, uid) in new[] { ("oid-a", uidA), ("oid-b", uidB) })
        {
            using var request = NewRequest(
                HttpMethod.Get,
                "/api/conversations",
                await SessionCookie(oid, uid.ToString("D")));
            using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);
            response.StatusCode.ShouldBe(HttpStatusCode.OK);
        }

        var seen = _factory.Destinations.Requests;
        seen.Count.ShouldBe(2);
        ReadBearer(seen[0]).GetClaim("uid").Value.ShouldBe(uidA.ToString("D"));
        ReadBearer(seen[1]).GetClaim("uid").Value.ShouldBe(uidB.ToString("D"));
    }

    [Theory]
    [InlineData("oid-1", null)]
    [InlineData(null, "7f1f6f7a-6c63-4a39-9c3a-3f2b6a6d2f10")]
    [InlineData("oid-1", "not-a-guid")]
    [InlineData("oid-1", "00000000-0000-0000-0000-000000000000")]
    public async Task IncompletePrincipalGets401AndNothingIsForwarded(string? oid, string? uid)
    {
        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(HttpMethod.Get, "/api/users/me", await SessionCookie(oid, uid));

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.Unauthorized);
        _factory.Destinations.Requests.ShouldBeEmpty();
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task WebCatchAllIsAnonymousAndSendsNeitherCookieNorBearer(bool withSession)
    {
        var cookie = withSession ? await SessionCookie("oid-1", Guid.NewGuid().ToString("D")) : null;
        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(HttpMethod.Get, "/c/some-id", cookie);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var seen = _factory.Destinations.Requests.ShouldHaveSingleItem();
        seen.Uri.Host.ShouldBe("web");
        seen.Uri.Port.ShouldBe(80);
        seen.Uri.AbsolutePath.ShouldBe("/c/some-id");
        seen.HasCookie.ShouldBeFalse();
        seen.Authorization.ShouldBeNull();
    }

    [Theory]
    [InlineData("/health/live", HttpStatusCode.OK)]
    [InlineData("/auth/login", HttpStatusCode.Redirect)]
    [InlineData("/.well-known/jwks.json", HttpStatusCode.NotFound)]
    public async Task SpecificAnonymousRoutesDoNotFallThroughToWeb(string path, HttpStatusCode expected)
    {
        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(HttpMethod.Get, path);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(expected);
        _factory.Destinations.Requests.ShouldBeEmpty();
    }

    [Fact]
    public async Task SseFramesAreRelayedWhileTheDestinationIsStillOpen()
    {
        var pipe = new Pipe();
        _factory.Destinations.Respond = _ =>
        {
            var content = new StreamContent(pipe.Reader.AsStream());
            content.Headers.ContentType = new MediaTypeHeaderValue("text/event-stream");
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
        };

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(15));

        using var client = _factory.CreateGatewayClient();
        using var request = NewRequest(
            HttpMethod.Post,
            "/api/conversations/abc/messages",
            await SessionCookie("oid-1", Guid.NewGuid().ToString("D")));

        await pipe.Writer.WriteAsync(Encoding.UTF8.GetBytes("event: token\ndata: Hel\n\n"), timeout.Token);

        using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, timeout.Token);
        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        response.Content.Headers.ContentType.ShouldNotBeNull().MediaType.ShouldBe("text/event-stream");

        await using var body = await response.Content.ReadAsStreamAsync(timeout.Token);
        var buffer = new byte[1024];
        var read = await body.ReadAsync(buffer, timeout.Token);
        Encoding.UTF8.GetString(buffer, 0, read).ShouldContain("event: token");

        await pipe.Writer.WriteAsync(Encoding.UTF8.GetBytes("event: done\ndata: {}\n\n"), timeout.Token);
        await pipe.Writer.CompleteAsync();

        using var reader = new StreamReader(body);
        var rest = await reader.ReadToEndAsync(timeout.Token);
        rest.ShouldContain("event: done");
    }
}
