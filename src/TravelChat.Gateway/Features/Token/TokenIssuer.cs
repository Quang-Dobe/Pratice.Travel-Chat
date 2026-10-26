using System.Security.Cryptography;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;

namespace TravelChat.Gateway.Features.Token;

public sealed class InternalTokenOptions
{
    public const string SectionName = "InternalToken";

    public string Issuer { get; set; } = "q-gateway";

    public string Audience { get; set; } = "q-internal";

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

public sealed class TokenIssuer : IDisposable
{
    private readonly RSA _rsa;
    private readonly SigningCredentials _credentials;
    private readonly JsonWebTokenHandler _handler = new();
    private readonly InternalTokenOptions _options;

    public TokenIssuer(IOptions<InternalTokenOptions> options)
    {
        _options = options.Value;
        _rsa = RSA.Create(2048);

        var keyId = Guid.NewGuid().ToString("N");
        _credentials = new SigningCredentials(
            new RsaSecurityKey(_rsa) { KeyId = keyId },
            SecurityAlgorithms.RsaSha256);

        var publicParameters = _rsa.ExportParameters(includePrivateParameters: false);
        PublicJwk = new PublicJwk(
            keyId,
            "RSA",
            Base64UrlEncoder.Encode(publicParameters.Modulus),
            Base64UrlEncoder.Encode(publicParameters.Exponent),
            SecurityAlgorithms.RsaSha256,
            "sig");
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
            Issuer = _options.Issuer,
            Audience = _options.Audience,
            Claims = claims,
            IssuedAt = issuedAt.UtcDateTime,
            Expires = issuedAt.Add(_options.Lifetime).UtcDateTime,
            SigningCredentials = _credentials,
        };

        return _handler.CreateToken(descriptor);
    }

    public void Dispose() => _rsa.Dispose();
}
