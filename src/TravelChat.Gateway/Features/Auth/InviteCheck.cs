using System.Net;
using System.Net.Http.Headers;
using System.Security.Claims;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authentication.OpenIdConnect;
using Microsoft.IdentityModel.Protocols.OpenIdConnect;
using TravelChat.Gateway.Features.Token;

namespace TravelChat.Gateway.Features.Auth;

public sealed record UserMeResponse([property: JsonPropertyName("id")] Guid Id);

public sealed partial class InviteCheck
{
    public const string OidClaim = "oid";
    public const string UidClaim = "uid";
    public const string DeniedPath = "/denied";

    private const string MappedOidClaim = "http://schemas.microsoft.com/identity/claims/objectidentifier";

    private static readonly Uri MePath = new("/users/me", UriKind.Relative);

    private readonly HttpClient _http;
    private readonly TokenIssuer _issuer;
    private readonly ILogger<InviteCheck> _logger;

    public InviteCheck(HttpClient http, TokenIssuer issuer, ILogger<InviteCheck> logger)
    {
        _http = http;
        _issuer = issuer;
        _logger = logger;
    }

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
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _issuer.Mint(oid));

            using var response = await _http.SendAsync(request, ct);
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

public static class InviteCheckRegistration
{
    private const string UserServiceKey = "ReverseProxy:Clusters:q-user:Destinations:primary:Address";

    public static Uri UserServiceAddress(IConfiguration configuration) =>
        new(configuration[UserServiceKey]
            ?? throw new InvalidOperationException($"{UserServiceKey} is not configured."));

    public static IServiceCollection AddInviteCheck(this IServiceCollection services, IConfiguration configuration)
    {
        var address = UserServiceAddress(configuration);
        services.AddHttpClient<InviteCheck>(client =>
        {
            client.BaseAddress = address;
            client.Timeout = TimeSpan.FromSeconds(3);
        });

        services.PostConfigure<OpenIdConnectOptions>(OpenIdConnectDefaults.AuthenticationScheme, options =>
        {
            options.SaveTokens = false;
            options.ResponseType = OpenIdConnectResponseType.Code;

            var existing = options.Events.OnTokenValidated;
            options.Events.OnTokenValidated = async context =>
            {
                await existing(context);
                if (context.Result?.Handled == true)
                {
                    return;
                }

                await context.HttpContext.RequestServices
                    .GetRequiredService<InviteCheck>()
                    .OnTokenValidatedAsync(context);
            };
        });

        return services;
    }
}
