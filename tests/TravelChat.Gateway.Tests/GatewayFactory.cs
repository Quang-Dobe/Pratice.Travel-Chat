using System.Collections.Concurrent;
using System.Globalization;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Claims;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authentication.OpenIdConnect;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.Protocols;
using Microsoft.IdentityModel.Protocols.OpenIdConnect;
using TravelChat.Gateway.Features.Auth;
using Yarp.ReverseProxy.Forwarder;

namespace TravelChat.Gateway.Tests;

public sealed class QUserStub : HttpMessageHandler
{
    public Func<HttpRequestMessage, HttpResponseMessage> Respond { get; set; } =
        _ => new HttpResponseMessage(HttpStatusCode.NotFound);

    public int CallCount { get; private set; }

    public Uri? LastRequestUri { get; private set; }

    public AuthenticationHeaderValue? LastAuthorization { get; private set; }

    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        CallCount++;
        LastRequestUri = request.RequestUri;
        LastAuthorization = request.Headers.Authorization;
        return Task.FromResult(Respond(request));
    }
}

public sealed record SeenRequest(
    HttpMethod Method,
    Uri Uri,
    AuthenticationHeaderValue? Authorization,
    bool HasCookie);

public sealed class DestinationStub : HttpMessageHandler
{
    private readonly ConcurrentQueue<SeenRequest> _requests = new();

    public Func<HttpRequestMessage, HttpResponseMessage> Respond { get; set; } = DefaultResponse;

    public IReadOnlyList<SeenRequest> Requests => [.. _requests];

    public void Reset()
    {
        _requests.Clear();
        Respond = DefaultResponse;
    }

    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        _requests.Enqueue(new SeenRequest(
            request.Method,
            request.RequestUri ?? new Uri("about:blank"),
            request.Headers.Authorization,
            request.Headers.Contains("Cookie")));
        return Task.FromResult(Respond(request));
    }

    private static HttpResponseMessage DefaultResponse(HttpRequestMessage request) =>
        new(HttpStatusCode.OK) { Content = new StringContent("ok") };
}

public sealed class StubForwarderClientFactory(HttpMessageHandler handler) : IForwarderHttpClientFactory
{
    public HttpMessageInvoker CreateClient(ForwarderHttpClientContext context) =>
        new(handler, disposeHandler: false);
}

public sealed class GatewayFactory : WebApplicationFactory<Program>
{
    public const int PublicPort = 8080;
    public const int InternalPort = 8081;
    public const string LocalPortHeader = "X-Test-Local-Port";
    public const string IdentityProviderAuthorizeEndpoint = "https://login.test/authorize";

    public QUserStub QUser { get; } = new();

    public DestinationStub Destinations { get; } = new();

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureAppConfiguration((_, configuration) =>
            configuration.AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["AzureAd:TenantId"] = "11111111-1111-1111-1111-111111111111",
                ["AzureAd:ClientId"] = "22222222-2222-2222-2222-222222222222",
                ["AzureAd:ClientSecret"] = "test-secret",
            }));

        builder.ConfigureServices(services =>
        {
            services.AddSingleton<IStartupFilter, LocalPortStartupFilter>();
            services.AddSingleton<IStartupFilter, TestEndpointsStartupFilter>();
            services.AddHttpClient<InviteCheck>().ConfigurePrimaryHttpMessageHandler(() => QUser);
            services.Replace(ServiceDescriptor.Singleton<IForwarderHttpClientFactory>(
                new StubForwarderClientFactory(Destinations)));
            services.PostConfigure<OpenIdConnectOptions>(OpenIdConnectDefaults.AuthenticationScheme, options =>
            {
                var configuration = new OpenIdConnectConfiguration
                {
                    Issuer = "https://login.test/",
                    AuthorizationEndpoint = IdentityProviderAuthorizeEndpoint,
                    TokenEndpoint = "https://login.test/token",
                };
                options.Configuration = configuration;
                options.ConfigurationManager = new StaticConfigurationManager<OpenIdConnectConfiguration>(configuration);
            });
        });
    }

    public HttpClient CreateGatewayClient() =>
        CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false, HandleCookies = false });

    public async Task<string> CreateSessionCookieAsync(string? oid, string? uid)
    {
        using var client = CreateGatewayClient();
        return await SignInCookieAsync(client, oid, uid);
    }

    public static async Task<string> SignInCookieAsync(HttpClient client, string? oid, string? uid)
    {
        var query = new List<string>();
        if (oid is not null)
        {
            query.Add($"oid={Uri.EscapeDataString(oid)}");
        }

        if (uid is not null)
        {
            query.Add($"uid={Uri.EscapeDataString(uid)}");
        }

        using var request = new HttpRequestMessage(
            HttpMethod.Get,
            new Uri($"{TestEndpointsStartupFilter.SignInPath}?{string.Join('&', query)}", UriKind.Relative));
        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        return string.Join(
            "; ",
            response.Headers.GetValues("Set-Cookie").Select(header => header.Split(';')[0]));
    }

    public sealed class TestEndpointsStartupFilter : IStartupFilter
    {
        public const string SignInPath = "/__test/signin";
        public const string WhoAmIPath = "/__test/whoami";

        public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next) => app =>
        {
            app.Use(async (context, nextMiddleware) =>
            {
                if (context.Request.Path == SignInPath)
                {
                    var claims = context.Request.Query
                        .Where(pair => pair.Key is "oid" or "uid")
                        .Select(pair => new Claim(pair.Key, pair.Value.ToString()));
                    await context.SignInAsync(
                        CookieAuthenticationDefaults.AuthenticationScheme,
                        new ClaimsPrincipal(new ClaimsIdentity(claims, CookieAuthenticationDefaults.AuthenticationScheme)));
                    return;
                }

                if (context.Request.Path == WhoAmIPath)
                {
                    var result = await context.AuthenticateAsync();
                    context.Response.StatusCode = result.Succeeded ? 200 : 401;
                    var cookies = string.Join(',', context.Request.Cookies.Keys);
                    await context.Response.WriteAsync(result.Succeeded
                        ? string.Join(',', result.Principal.Claims.Select(claim => $"{claim.Type}={claim.Value}"))
                        : $"{result.Failure?.Message ?? "no result"}; cookies received: [{cookies}]");
                    return;
                }

                await nextMiddleware(context);
            });

            next(app);
        };
    }

    public HttpClient CreateClientOnPort(int localPort)
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Add(
            LocalPortHeader,
            localPort.ToString(CultureInfo.InvariantCulture));
        return client;
    }

    public sealed class LocalPortStartupFilter : IStartupFilter
    {
        public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next) => app =>
        {
            app.Use((context, nextMiddleware) =>
            {
                if (context.Request.Headers.TryGetValue(LocalPortHeader, out var value)
                    && int.TryParse(value.ToString(), NumberStyles.None, CultureInfo.InvariantCulture, out var port))
                {
                    context.Connection.LocalPort = port;
                }

                return nextMiddleware(context);
            });

            next(app);
        };
    }
}
