using System.Net;
using System.Security.Claims;
using System.Text;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authentication.OpenIdConnect;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.JsonWebTokens;
using Shouldly;
using TravelChat.Gateway.Features.Auth;
using TravelChat.Gateway.Features.Token;

namespace TravelChat.Gateway.Tests;

public sealed class InviteCheckTests(GatewayFactory factory) : IClassFixture<GatewayFactory>
{
    private const string MappedOidClaim = "http://schemas.microsoft.com/identity/claims/objectidentifier";

    private static HttpResponseMessage Json(HttpStatusCode status, string body) =>
        new(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    private static TokenValidatedContext NewContext(HttpContext httpContext, ClaimsPrincipal principal) =>
        new(
            httpContext,
            new AuthenticationScheme(
                OpenIdConnectDefaults.AuthenticationScheme,
                null,
                typeof(OpenIdConnectHandler)),
            new OpenIdConnectOptions(),
            principal,
            new AuthenticationProperties());

    private static async Task<TokenValidatedContext> RunAsync(QUserStub stub, ClaimsPrincipal principal)
    {
        using var issuer = new TokenIssuer(Options.Create(new InternalTokenOptions()));
        using var httpClient = new HttpClient(stub, disposeHandler: false)
        {
            BaseAddress = InviteCheck.UserServiceAddress,
        };
        var check = new InviteCheck(httpClient, issuer, NullLogger<InviteCheck>.Instance);

        var context = NewContext(new DefaultHttpContext(), principal);
        await check.OnTokenValidatedAsync(context);
        return context;
    }

    private async Task<TokenValidatedContext> RunRegisteredHandlerAsync(ClaimsPrincipal principal)
    {
        await using var scope = factory.Services.CreateAsyncScope();
        var httpContext = new DefaultHttpContext { RequestServices = scope.ServiceProvider };
        var options = factory.Services
            .GetRequiredService<IOptionsMonitor<OpenIdConnectOptions>>()
            .Get(OpenIdConnectDefaults.AuthenticationScheme);

        var context = NewContext(httpContext, principal);
        await options.Events.OnTokenValidated(context);
        return context;
    }

    private static ClaimsPrincipal EntraPrincipal(string claimType = "oid", string? oid = "oid-1") =>
        new(new ClaimsIdentity(
            [
                new Claim(claimType, oid ?? string.Empty),
                new Claim("name", "Ada"),
                new Claim("tid", "tenant"),
                new Claim("preferred_username", "ada@example.test"),
            ],
            "AuthenticationTypes.Federation"));

    private static void ShouldBeDenied(TokenValidatedContext context)
    {
        context.Result.ShouldNotBeNull().Handled.ShouldBeTrue();
        context.Response.StatusCode.ShouldBe(StatusCodes.Status302Found);
        context.Response.Headers.Location.ToString().ShouldBe("/denied");
        context.Response.Headers.ContainsKey("Set-Cookie").ShouldBeFalse();
    }

    [Theory]
    [InlineData(HttpStatusCode.NotFound)]
    [InlineData(HttpStatusCode.Unauthorized)]
    [InlineData(HttpStatusCode.InternalServerError)]
    [InlineData(HttpStatusCode.ServiceUnavailable)]
    public async Task NonOkAnswerRedirectsToDeniedWithoutCookie(HttpStatusCode status)
    {
        var stub = new QUserStub { Respond = _ => new HttpResponseMessage(status) };

        ShouldBeDenied(await RunAsync(stub, EntraPrincipal()));
    }

    [Fact]
    public async Task TimeoutRedirectsToDenied()
    {
        var stub = new QUserStub
        {
            Respond = _ => throw new TaskCanceledException("timeout", new TimeoutException()),
        };

        ShouldBeDenied(await RunAsync(stub, EntraPrincipal()));
    }

    [Fact]
    public async Task TransportExceptionRedirectsToDenied()
    {
        var stub = new QUserStub { Respond = _ => throw new HttpRequestException("down") };

        ShouldBeDenied(await RunAsync(stub, EntraPrincipal()));
    }

    [Fact]
    public async Task UnsupportedContentTypeRedirectsToDenied()
    {
        var stub = new QUserStub
        {
            Respond = _ => new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent("plain", Encoding.UTF8, "text/plain"),
            },
        };

        ShouldBeDenied(await RunAsync(stub, EntraPrincipal()));
    }

    [Theory]
    [InlineData("not json")]
    [InlineData("{}")]
    [InlineData("{\"id\":\"00000000-0000-0000-0000-000000000000\"}")]
    public async Task UnusableBodyRedirectsToDenied(string body)
    {
        var stub = new QUserStub { Respond = _ => Json(HttpStatusCode.OK, body) };

        ShouldBeDenied(await RunAsync(stub, EntraPrincipal()));
    }

    [Fact]
    public async Task MissingOidIsDeniedWithoutCallingQUser()
    {
        var stub = new QUserStub();
        var principal = new ClaimsPrincipal(new ClaimsIdentity([new Claim("name", "Ada")], "test"));

        ShouldBeDenied(await RunAsync(stub, principal));
        stub.CallCount.ShouldBe(0);
    }

    [Theory]
    [InlineData("oid")]
    [InlineData(MappedOidClaim)]
    public async Task OkAnswerKeepsOnlyOidAndUid(string claimType)
    {
        var uid = Guid.NewGuid();
        var stub = new QUserStub
        {
            Respond = _ => Json(
                HttpStatusCode.OK,
                $$"""{"id":"{{uid}}","name":"Ada","email":"ada@example.test"}"""),
        };

        var context = await RunAsync(stub, EntraPrincipal(claimType));

        context.Result.ShouldBeNull();
        var claims = context.Principal.ShouldNotBeNull().Claims.ToList();
        claims.Count.ShouldBe(2);
        claims.ShouldContain(c => c.Type == "oid" && c.Value == "oid-1");
        claims.ShouldContain(c => c.Type == "uid" && c.Value == uid.ToString("D"));
        context.Response.Headers.ContainsKey("Set-Cookie").ShouldBeFalse();
    }

    [Fact]
    public async Task CallsQUserWithBearerForSubjectOnlyAndNoUid()
    {
        var stub = new QUserStub
        {
            Respond = _ => Json(HttpStatusCode.OK, $$"""{"id":"{{Guid.NewGuid()}}"}"""),
        };

        await RunAsync(stub, EntraPrincipal());

        stub.LastRequestUri.ShouldNotBeNull().AbsolutePath.ShouldBe("/users/me");
        var authorization = stub.LastAuthorization.ShouldNotBeNull();
        authorization.Scheme.ShouldBe("Bearer");
        var jwt = new JsonWebTokenHandler().ReadJsonWebToken(authorization.Parameter.ShouldNotBeNull());
        jwt.Subject.ShouldBe("oid-1");
        jwt.Claims.ShouldNotContain(c => c.Type == "uid");
    }

    [Fact]
    public async Task RegisteredOidcHandlerDeniesWhenQUserAnswers404()
    {
        factory.QUser.Respond = _ => new HttpResponseMessage(HttpStatusCode.NotFound);

        var context = await RunRegisteredHandlerAsync(EntraPrincipal());

        ShouldBeDenied(context);
        context.Principal.ShouldNotBeNull().Claims.ShouldNotContain(c => c.Type == "uid");
    }

    [Fact]
    public async Task RegisteredOidcHandlerKeepsOnlyOidAndUidWhenQUserAnswers200()
    {
        var uid = Guid.NewGuid();
        factory.QUser.Respond = _ => Json(HttpStatusCode.OK, $$"""{"id":"{{uid}}"}""");

        var context = await RunRegisteredHandlerAsync(EntraPrincipal());

        context.Result?.Handled.ShouldNotBe(true);
        var claims = context.Principal.ShouldNotBeNull().Claims.ToList();
        claims.Count.ShouldBe(2);
        claims.ShouldContain(c => c.Type == "oid" && c.Value == "oid-1");
        claims.ShouldContain(c => c.Type == "uid" && c.Value == uid.ToString("D"));
    }

    [Fact]
    public void SessionCookieIsHttpOnlyAndLax()
    {
        var options = factory.Services
            .GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>()
            .Get(CookieAuthenticationDefaults.AuthenticationScheme);

        options.Cookie.HttpOnly.ShouldBeTrue();
        options.Cookie.SameSite.ShouldBe(SameSiteMode.Lax);
        options.Cookie.SecurePolicy.ShouldBe(CookieSecurePolicy.SameAsRequest);
    }

    [Fact]
    public void OidcUsesCodeFlowAndDoesNotSaveTokens()
    {
        var options = factory.Services
            .GetRequiredService<IOptionsMonitor<OpenIdConnectOptions>>()
            .Get(OpenIdConnectDefaults.AuthenticationScheme);

        options.SaveTokens.ShouldBeFalse();
        options.ResponseType.ShouldBe("code");
        options.CallbackPath.Value.ShouldBe("/signin-oidc");
    }

    [Fact]
    public async Task LogoutAlwaysReturnsNoContent()
    {
        using var client = factory.CreateClient();
        using var request = new HttpRequestMessage(HttpMethod.Post, new Uri("/auth/logout", UriKind.Relative));

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        response.StatusCode.ShouldBe(HttpStatusCode.NoContent);
    }
}
