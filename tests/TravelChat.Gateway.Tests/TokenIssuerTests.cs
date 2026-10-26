using System.Net;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using Shouldly;
using TravelChat.Gateway.Features.Token;

namespace TravelChat.Gateway.Tests;

public sealed class TokenIssuerTests(GatewayFactory factory) : IClassFixture<GatewayFactory>
{
    private const string JwksPath = "/.well-known/jwks.json";
    private const string DiscoveryPath = "/.well-known/openid-configuration";

    private static readonly string[] PrivateMembers = ["d", "p", "q", "dp", "dq", "qi"];

    private TokenIssuer Issuer => factory.Services.GetRequiredService<TokenIssuer>();

    private static Uri Relative(string path) => new(path, UriKind.Relative);

    private static async Task<HttpResponseMessage> GetAsync(HttpClient client, string path, string? host = null)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, Relative(path));
        if (host is not null)
        {
            request.Headers.Host = host;
        }

        return await client.SendAsync(request, TestContext.Current.CancellationToken);
    }

    [Fact]
    public void MintSetsEveryClaim()
    {
        var issuer = Issuer;
        var uid = Guid.NewGuid();

        var jwt = new JsonWebTokenHandler().ReadJsonWebToken(issuer.Mint("oid-1", uid));

        jwt.Issuer.ShouldBe("q-gateway");
        jwt.Audiences.ShouldHaveSingleItem().ShouldBe("q-internal");
        jwt.Subject.ShouldBe("oid-1");
        jwt.GetPayloadValue<string>("uid").ShouldBe(uid.ToString("D"));
        jwt.Alg.ShouldBe("RS256");
        jwt.Kid.ShouldBe(issuer.PublicJwk.Kid);
        jwt.GetPayloadValue<long>("iat").ShouldBeGreaterThan(0);
        jwt.GetPayloadValue<long>("exp").ShouldBeGreaterThan(jwt.GetPayloadValue<long>("iat"));
    }

    [Fact]
    public void MintOmitsUidForInviteLookup()
    {
        var jwt = new JsonWebTokenHandler().ReadJsonWebToken(Issuer.Mint("oid-1"));

        jwt.Claims.ShouldNotContain(claim => claim.Type == "uid");
        jwt.Subject.ShouldBe("oid-1");
    }

    [Theory]
    [InlineData("")]
    [InlineData("  ")]
    public void MintRejectsBlankOid(string oid)
    {
        Should.Throw<ArgumentException>(() => Issuer.Mint(oid));
    }

    [Fact]
    public void MintExpiresFiveMinutesAfterIssue()
    {
        var jwt = new JsonWebTokenHandler().ReadJsonWebToken(Issuer.Mint("oid-1", Guid.NewGuid()));

        (jwt.GetPayloadValue<long>("exp") - jwt.GetPayloadValue<long>("iat")).ShouldBe(300);
    }

    [Fact]
    public async Task MintedTokenValidatesWithJwksFetchedFromInternalPort()
    {
        var uid = Guid.NewGuid();
        var token = factory.Services.GetRequiredService<TokenIssuer>().Mint("oid-1", uid);

        using var client = factory.CreateClientOnPort(GatewayFactory.InternalPort);
        using var response = await GetAsync(client, JwksPath);
        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var keySet = new JsonWebKeySet(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));

        var result = await new JsonWebTokenHandler().ValidateTokenAsync(
            token,
            new TokenValidationParameters
            {
                IssuerSigningKeys = keySet.GetSigningKeys(),
                ValidIssuer = "q-gateway",
                ValidAudience = "q-internal",
                ValidAlgorithms = ["RS256"],
            });

        result.IsValid.ShouldBeTrue();
    }

    [Fact]
    public async Task JwksHoldsOnlyPublicMembers()
    {
        using var client = factory.CreateClientOnPort(GatewayFactory.InternalPort);
        using var response = await GetAsync(client, JwksPath);
        response.StatusCode.ShouldBe(HttpStatusCode.OK);

        using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
        var key = document.RootElement.GetProperty("keys").EnumerateArray().ShouldHaveSingleItem();

        key.GetProperty("kty").GetString().ShouldBe("RSA");
        key.GetProperty("alg").GetString().ShouldBe("RS256");
        key.GetProperty("use").GetString().ShouldBe("sig");
        key.GetProperty("kid").GetString().ShouldNotBeNullOrWhiteSpace();
        key.GetProperty("n").GetString().ShouldNotBeNullOrWhiteSpace();
        key.GetProperty("e").GetString().ShouldNotBeNullOrWhiteSpace();
        foreach (var member in PrivateMembers)
        {
            key.TryGetProperty(member, out _).ShouldBeFalse();
        }
    }

    [Fact]
    public async Task DiscoveryPointsAtInternalJwks()
    {
        using var client = factory.CreateClientOnPort(GatewayFactory.InternalPort);
        using var response = await GetAsync(client, DiscoveryPath);
        response.StatusCode.ShouldBe(HttpStatusCode.OK);

        using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));

        document.RootElement.GetProperty("issuer").GetString().ShouldBe("q-gateway");
        document.RootElement.GetProperty("jwks_uri").GetString()
            .ShouldBe("http://q-gateway:8081/.well-known/jwks.json");
    }

    [Theory]
    [InlineData(JwksPath, null)]
    [InlineData(DiscoveryPath, null)]
    [InlineData(JwksPath, "localhost:8081")]
    [InlineData(DiscoveryPath, "localhost:8081")]
    public async Task WellKnownRoutesAreNotFoundOnPublicPort(string path, string? host)
    {
        using var client = factory.CreateClientOnPort(GatewayFactory.PublicPort);

        using var response = await GetAsync(client, path, host);

        response.StatusCode.ShouldBe(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task HealthLiveIsAnonymousOnPublicPort()
    {
        using var client = factory.CreateClientOnPort(GatewayFactory.PublicPort);

        using var response = await GetAsync(client, "/health/live");

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
    }
}
