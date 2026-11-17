using System.Globalization;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.Routing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using Shouldly;
using TravelChat.User.Data;
using TravelChat.User.Features.CurrentUser;

namespace TravelChat.User.Tests;

public sealed class UserApiFactory(string connectionString, string usersFile, RsaSecurityKey signingKey)
    : WebApplicationFactory<Program>
{
    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseSetting("ConnectionStrings:Users", connectionString);
        builder.UseSetting("Seed:UsersFile", usersFile);
        builder.ConfigureTestServices(services =>
            services.PostConfigure<JwtBearerOptions>(JwtBearerDefaults.AuthenticationScheme, options =>
            {
                options.Authority = null;
                options.ConfigurationManager = null;
                options.TokenValidationParameters.IssuerSigningKey = signingKey;
            }));
    }
}

[Collection("postgres")]
public sealed class MeEndpointTests(PostgresFixture postgres) : IAsyncLifetime
{
    private const string KeyId = "test-key";
    private const string Issuer = "q-gateway";
    private const string Audience = "q-internal";
    private const string Oid = "11111111-1111-1111-1111-111111111111";

    private readonly RSA _rsa = RSA.Create(2048);
    private readonly RSA _otherRsa = RSA.Create(2048);
    private string _usersFile = string.Empty;
    private UserApiFactory _factory = null!;

    public async ValueTask InitializeAsync()
    {
        await using var db = postgres.CreateContext();
        await db.Users.ExecuteDeleteAsync(TestContext.Current.CancellationToken);

        _usersFile = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName() + ".json");
        await File.WriteAllTextAsync(_usersFile, "[]", TestContext.Current.CancellationToken);
        _factory = new UserApiFactory(
            postgres.ConnectionString,
            _usersFile,
            new RsaSecurityKey(_rsa) { KeyId = KeyId });
        _ = _factory.Services;
    }

    public async ValueTask DisposeAsync()
    {
        await _factory.DisposeAsync();
        _rsa.Dispose();
        _otherRsa.Dispose();
        File.Delete(_usersFile);
    }

    [Fact]
    public async Task ActiveUserReturns200WithIdNameAndEmail()
    {
        var id = await AddUserAsync(Oid, "Active User", "active@example.invalid", isActive: true);

        var response = await GetMeAsync(Token(Oid));

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<MeResponse>(TestContext.Current.CancellationToken);
        body.ShouldNotBeNull();
        body.Id.ShouldBe(id);
        body.Name.ShouldBe("Active User");
        body.Email.ShouldBe("active@example.invalid");
    }

    [Fact]
    public async Task InactiveUserReturns404()
    {
        await AddUserAsync(Oid, "Gone", "gone@example.invalid", isActive: false);

        var response = await GetMeAsync(Token(Oid));

        response.StatusCode.ShouldBe(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task UnknownUserReturns404()
    {
        var response = await GetMeAsync(Token(Oid));

        response.StatusCode.ShouldBe(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task TokenWithoutUidStillResolvesBySub()
    {
        await AddUserAsync(Oid, "Active User", "active@example.invalid", isActive: true);

        var response = await GetMeAsync(Token(Oid));

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
    }

    [Fact]
    public async Task TokenWithUidStillResolvesBySub()
    {
        var id = await AddUserAsync(Oid, "Active User", "active@example.invalid", isActive: true);

        var response = await GetMeAsync(Token(Oid, uid: Guid.NewGuid().ToString()));

        response.StatusCode.ShouldBe(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<MeResponse>(TestContext.Current.CancellationToken);
        body.ShouldNotBeNull();
        body.Id.ShouldBe(id);
    }

    [Fact]
    public async Task UnsignedTokenReturns401()
    {
        await AddUserAsync(Oid, "Active User", "active@example.invalid", isActive: true);

        var response = await GetMeAsync(UnsignedToken(Oid));

        response.StatusCode.ShouldBe(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task TokenWithoutSubReturns401()
    {
        var response = await GetMeAsync(Token(sub: null));

        response.StatusCode.ShouldBe(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task NoTokenReturns401()
    {
        var response = await GetMeAsync(token: null);

        response.StatusCode.ShouldBe(HttpStatusCode.Unauthorized);
    }

    [Theory]
    [InlineData("signature")]
    [InlineData("audience")]
    [InlineData("issuer")]
    [InlineData("expired")]
    public async Task InvalidTokenReturns401(string defect)
    {
        await AddUserAsync(Oid, "Active User", "active@example.invalid", isActive: true);
        var token = defect switch
        {
            "signature" => Token(Oid, signWith: _otherRsa),
            "audience" => Token(Oid, audience: "someone-else"),
            "issuer" => Token(Oid, issuer: "someone-else"),
            _ => Token(Oid, expired: true),
        };

        var response = await GetMeAsync(token);

        response.StatusCode.ShouldBe(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public void HealthLiveIsTheOnlyAnonymousEndpoint()
    {
        var anonymous = _factory.Services
            .GetServices<EndpointDataSource>()
            .SelectMany(source => source.Endpoints)
            .OfType<RouteEndpoint>()
            .Where(endpoint => endpoint.Metadata.GetMetadata<IAllowAnonymous>() is not null)
            .Select(endpoint => endpoint.RoutePattern.RawText);

        anonymous.ShouldBe(["/health/live"]);
    }

    private async Task<Guid> AddUserAsync(string oid, string name, string email, bool isActive)
    {
        await using var db = postgres.CreateContext();
        var user = new AppUser { Id = Guid.NewGuid(), Oid = oid, Name = name, Email = email, IsActive = isActive };
        db.Users.Add(user);
        await db.SaveChangesAsync(TestContext.Current.CancellationToken);
        return user.Id;
    }

    private async Task<HttpResponseMessage> GetMeAsync(string? token)
    {
        using var client = _factory.CreateClient();
        if (token is not null)
        {
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }

        return await client.GetAsync("/users/me", TestContext.Current.CancellationToken);
    }

    private string Token(
        string? sub,
        string issuer = Issuer,
        string audience = Audience,
        bool expired = false,
        RSA? signWith = null,
        string? uid = null)
    {
        var now = DateTime.UtcNow;
        var claims = new Dictionary<string, object>();
        if (sub is not null)
        {
            claims["sub"] = sub;
        }

        if (uid is not null)
        {
            claims["uid"] = uid;
        }

        var descriptor = new SecurityTokenDescriptor
        {
            Issuer = issuer,
            Audience = audience,
            NotBefore = expired ? now.AddHours(-2) : now.AddMinutes(-1),
            Expires = expired ? now.AddHours(-1) : now.AddMinutes(10),
            SigningCredentials = new SigningCredentials(
                new RsaSecurityKey(signWith ?? _rsa) { KeyId = KeyId },
                SecurityAlgorithms.RsaSha256),
            Claims = claims,
        };

        return new JsonWebTokenHandler().CreateToken(descriptor);
    }

    private static string UnsignedToken(string sub)
    {
        var expires = DateTimeOffset.UtcNow.AddMinutes(10).ToUnixTimeSeconds().ToString(CultureInfo.InvariantCulture);
        var header = Base64UrlEncoder.Encode("""{"alg":"none","typ":"JWT"}""");
        var payload = Base64UrlEncoder.Encode(
            $$"""{"sub":"{{sub}}","iss":"{{Issuer}}","aud":"{{Audience}}","exp":{{expires}}}""");
        return $"{header}.{payload}.";
    }
}
