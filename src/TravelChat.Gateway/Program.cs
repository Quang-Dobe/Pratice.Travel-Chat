using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authentication.OpenIdConnect;
using Microsoft.AspNetCore.Authorization;
using Microsoft.Identity.Web;
using TravelChat.Gateway.Features.Auth;
using TravelChat.Gateway.Features.Maps;
using TravelChat.Gateway.Features.Proxy;
using TravelChat.Gateway.Features.Token;
using TravelChat.ServiceDefaults;

var builder = WebApplication.CreateBuilder(args);
builder.AddServiceDefaults();

builder.Services.Configure<InternalTokenOptions>(
    builder.Configuration.GetSection(InternalTokenOptions.SectionName));
builder.Services.AddSingleton<TokenIssuer>();
builder.Services.Configure<MapsOptions>(
    builder.Configuration.GetSection(MapsOptions.SectionName));

builder.Services
    .AddAuthentication(options =>
    {
        options.DefaultScheme = CookieAuthenticationDefaults.AuthenticationScheme;
        options.DefaultChallengeScheme = OpenIdConnectDefaults.AuthenticationScheme;
    })
    .AddMicrosoftIdentityWebApp(builder.Configuration.GetSection("AzureAd"));
builder.Services.PostConfigure<CookieAuthenticationOptions>(
    CookieAuthenticationDefaults.AuthenticationScheme,
    options =>
    {
        options.Cookie.HttpOnly = true;
        options.Cookie.SameSite = SameSiteMode.Lax;
        options.Cookie.SecurePolicy = CookieSecurePolicy.SameAsRequest;
    });
builder.Services.AddInviteCheck(builder.Configuration);
builder.Services.AddApiAuthStatusCodes();
builder.Services.AddAuthorizationBuilder()
    .SetFallbackPolicy(new AuthorizationPolicyBuilder().RequireAuthenticatedUser().Build())
    .AddPolicy(ApiAuthStatusCodes.PolicyName, policy => policy
        .AddAuthenticationSchemes(CookieAuthenticationDefaults.AuthenticationScheme)
        .RequireAuthenticatedUser());
builder.Services
    .AddReverseProxy()
    .LoadFromConfig(builder.Configuration.GetSection("ReverseProxy"))
    .AddTokenTransform()
    .AddMapsTransform();

var app = builder.Build();
app.UseAuthentication();
app.UseAuthorization();
app.MapDefaultEndpoints();
app.MapJwksEndpoints();
app.MapAuthEndpoints();
app.MapApiFallback();
app.MapReverseProxy();
app.Run();

public partial class Program;
