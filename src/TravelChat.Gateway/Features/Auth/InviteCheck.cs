using System.Net;
using System.Net.Http.Headers;
using System.Security.Claims;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authentication.OpenIdConnect;
using TravelChat.Gateway.Features.Token;

namespace TravelChat.Gateway.Features.Auth;

public sealed record UserMeResponse([property: JsonPropertyName("id")] Guid Id);

public sealed partial class InviteCheck(HttpClient http, TokenIssuer issuer, ILogger<InviteCheck> logger)
{
    public const string OidClaim = "oid";
    public const string UidClaim = "uid";
    public const string DeniedPath = "/denied";

    private const string MappedOidClaim = "http://schemas.microsoft.com/identity/claims/objectidentifier";

    private static readonly Uri MePath = new("/users/me", UriKind.Relative);

    public async Task<Guid?> CheckAsync(string? oid, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(oid))
        {
            LogDenied("missing oid claim");
            return null;
        }

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, MePath);
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", issuer.Mint(oid));

            using var response = await http.SendAsync(request, ct);
            if (response.StatusCode != HttpStatusCode.OK)
            {
                LogDenied($"q-user answered {(int)response.StatusCode}");
                return null;
            }

            var me = await response.Content.ReadFromJsonAsync<UserMeResponse>(ct);
            if (me is { Id: var id } && id != Guid.Empty)
            {
                return id;
            }

            LogDenied("q-user returned no user id");
            return null;
        }
        catch (Exception ex) when (ex is HttpRequestException
            or OperationCanceledException
            or JsonException
            or InvalidOperationException
            or NotSupportedException)
        {
            LogDenied($"q-user call failed: {ex.GetType().Name}");
            return null;
        }
    }

    public async Task OnTokenValidatedAsync(TokenValidatedContext context)
    {
        var oid = context.Principal?.FindFirst(OidClaim)?.Value
            ?? context.Principal?.FindFirst(MappedOidClaim)?.Value;

        if (oid is null || await CheckAsync(oid, context.HttpContext.RequestAborted) is not { } uid)
        {
            context.Response.Redirect(DeniedPath);
            context.HandleResponse();
            return;
        }

        var identity = new ClaimsIdentity(
            [new Claim(OidClaim, oid), new Claim(UidClaim, uid.ToString("D"))],
            CookieAuthenticationDefaults.AuthenticationScheme);
        context.Principal = new ClaimsPrincipal(identity);
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Sign-in denied: {Reason}")]
    private partial void LogDenied(string reason);
}
