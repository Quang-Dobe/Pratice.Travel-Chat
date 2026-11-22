using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using TravelChat.ServiceDefaults;

namespace TravelChat.Gateway.Features.Token;

public sealed class InternalTokenOptions
{
    public const string SectionName = "InternalToken";

    public TimeSpan Lifetime { get; set; } = TimeSpan.FromMinutes(5);

    public int InternalPort { get; set; } = 8081;

    public Uri InternalBaseUrl { get; set; } = new("http://q-gateway:8081");
}

public sealed record PublicJwk(
    [property: JsonPropertyName("kid")] string Kid,
    [property: JsonPropertyName("kty")] string Kty,
    [property: JsonPropertyName("n")] string N,
    [property: JsonPropertyName("e")] string E,
    [property: JsonPropertyName("alg")] string Alg,
    [property: JsonPropertyName("use")] string Use);

public static class TokenIssuerRegistration
{
    public static IServiceCollection AddTokenIssuer(this IServiceCollection services)
    {
        services.AddOptions<InternalTokenOptions>().BindConfiguration(InternalTokenOptions.SectionName);
        services.AddSingleton<ISigningKeySlot, RedisSigningKeySlot>();
        services.AddSingleton<SigningKeyStore>();
        services.AddSingleton(provider => new TokenIssuer(
            provider.GetRequiredService<IOptions<InternalTokenOptions>>(),
            provider.GetRequiredService<SigningKeyStore>().LoadOrCreate()));
        return services;
    }
}

public sealed class TokenIssuer : IDisposable
{
    private readonly RSA _rsa;
    private readonly SigningCredentials _credentials;
    private readonly JsonWebTokenHandler _handler = new();
    private readonly InternalTokenOptions _options;

    public TokenIssuer(IOptions<InternalTokenOptions> options, RSA key)
    {
        _options = options.Value;
        _rsa = key;

        var publicParameters = _rsa.ExportParameters(includePrivateParameters: false);
        var modulus = Base64UrlEncoder.Encode(publicParameters.Modulus);
        var exponent = Base64UrlEncoder.Encode(publicParameters.Exponent);
        var keyId = Thumbprint(exponent, modulus);

        _credentials = new SigningCredentials(
            new RsaSecurityKey(_rsa) { KeyId = keyId },
            SecurityAlgorithms.RsaSha256);

        PublicJwk = new PublicJwk(keyId, "RSA", modulus, exponent, SecurityAlgorithms.RsaSha256, "sig");
    }

    private static string Thumbprint(string exponent, string modulus)
    {
        var canonical = $$"""{"e":"{{exponent}}","kty":"RSA","n":"{{modulus}}"}""";
        return Base64UrlEncoder.Encode(SHA256.HashData(Encoding.UTF8.GetBytes(canonical)));
    }

    public PublicJwk PublicJwk { get; }

    public string Mint(string oid, Guid? uid = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(oid);

        var issuedAt = DateTimeOffset.FromUnixTimeSeconds(DateTimeOffset.UtcNow.ToUnixTimeSeconds());

        var claims = new Dictionary<string, object> { ["sub"] = oid };
        if (uid is { } userId)
        {
            claims["uid"] = userId.ToString("D");
        }

        var descriptor = new SecurityTokenDescriptor
        {
            Issuer = InternalToken.Issuer,
            Audience = InternalToken.Audience,
            Claims = claims,
            IssuedAt = issuedAt.UtcDateTime,
            Expires = issuedAt.Add(_options.Lifetime).UtcDateTime,
            SigningCredentials = _credentials,
        };

        return _handler.CreateToken(descriptor);
    }

    public void Dispose() => _rsa.Dispose();
}
