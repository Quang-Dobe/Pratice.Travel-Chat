using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authentication.OpenIdConnect;
using Microsoft.AspNetCore.Authorization;
using Microsoft.Identity.Web;
using Microsoft.IdentityModel.Protocols.OpenIdConnect;
using TravelChat.Gateway.Features.Api;

namespace TravelChat.Gateway.Features.Auth;

public static class AuthSetup
{
    private const string UserServiceKey = "ReverseProxy:Clusters:q-user:Destinations:primary:Address";

    public static Uri UserServiceAddress(IConfiguration configuration) =>
        new(configuration[UserServiceKey]
            ?? throw new InvalidOperationException($"{UserServiceKey} is not configured."));

    public static IServiceCollection AddGatewayAuth(this IServiceCollection services, IConfiguration configuration)
    {
        services
            .AddAuthentication(options =>
            {
                options.DefaultScheme = CookieAuthenticationDefaults.AuthenticationScheme;
                options.DefaultChallengeScheme = OpenIdConnectDefaults.AuthenticationScheme;
            })
            .AddMicrosoftIdentityWebApp(configuration.GetSection("AzureAd"));

        services.PostConfigure<CookieAuthenticationOptions>(
            CookieAuthenticationDefaults.AuthenticationScheme,
            options =>
            {
                options.Cookie.HttpOnly = true;
                options.Cookie.SameSite = SameSiteMode.Lax;
                options.Cookie.SecurePolicy = CookieSecurePolicy.SameAsRequest;

                var existingLogin = options.Events.OnRedirectToLogin;
                options.Events.OnRedirectToLogin = context =>
                    context.Request.Path.StartsWithSegments(ApiRoutes.Prefix)
                        ? ApiProblems.WriteAsync(context.HttpContext, StatusCodes.Status401Unauthorized).AsTask()
                        : existingLogin(context);

                var existingDenied = options.Events.OnRedirectToAccessDenied;
                options.Events.OnRedirectToAccessDenied = context =>
                    context.Request.Path.StartsWithSegments(ApiRoutes.Prefix)
                        ? ApiProblems.WriteAsync(context.HttpContext, StatusCodes.Status403Forbidden).AsTask()
                        : existingDenied(context);
            });

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

        services.AddAuthorizationBuilder()
            .SetFallbackPolicy(new AuthorizationPolicyBuilder().RequireAuthenticatedUser().Build())
            .AddPolicy(ApiRoutes.Policy, policy => policy
                .AddAuthenticationSchemes(CookieAuthenticationDefaults.AuthenticationScheme)
                .RequireAuthenticatedUser());

        return services;
    }
}
