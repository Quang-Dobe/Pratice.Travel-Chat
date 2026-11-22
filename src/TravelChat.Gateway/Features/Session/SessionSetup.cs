using Azure.Identity;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.DataProtection.KeyManagement;
using Microsoft.AspNetCore.DataProtection.StackExchangeRedis;
using Microsoft.Extensions.Caching.StackExchangeRedis;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;
using StackExchange.Redis;
using TravelChat.Gateway.Features.Api;

namespace TravelChat.Gateway.Features.Session;

public static class SessionSetup
{
    public const string TimeLeftPath = "/api/session";

    private const string KeyVaultKeyIdSetting = "DataProtection:KeyVaultKeyId";
    private const string CacheInstancePrefix = "gateway:session:";
    private const string DataProtectionApplicationName = "TravelChat.Gateway";
    private const string DataProtectionKeyRing = "gateway:dp-keys";

    public static IServiceCollection AddGatewaySessions(this IServiceCollection services, IConfiguration configuration)
    {
        var redis = configuration.GetConnectionString("Redis");
        if (string.IsNullOrWhiteSpace(redis))
        {
            throw new InvalidOperationException("ConnectionStrings:Redis is not configured.");
        }

        services.AddOptions<GatewaySessionOptions>()
            .BindConfiguration(GatewaySessionOptions.SectionName)
            .Validate(
                GatewaySessionOptions.IsValid,
                "Session:IdleTimeout and Session:AbsoluteLimit must be above zero, and AbsoluteLimit must not be below IdleTimeout.")
            .ValidateOnStart();

        services.TryAddSingleton(TimeProvider.System);

        services.AddSingleton<IConnectionMultiplexer>(_ =>
        {
            var options = ConfigurationOptions.Parse(redis);
            options.AbortOnConnectFail = false;
            return ConnectionMultiplexer.Connect(options);
        });

        services.AddStackExchangeRedisCache(options => options.InstanceName = CacheInstancePrefix);
        services.AddOptions<RedisCacheOptions>().Configure<IServiceProvider>((options, provider) =>
            options.ConnectionMultiplexerFactory = () =>
                Task.FromResult(provider.GetRequiredService<IConnectionMultiplexer>()));

        var dataProtection = services.AddDataProtection().SetApplicationName(DataProtectionApplicationName);
        var keyVaultKeyId = configuration[KeyVaultKeyIdSetting];
        if (!string.IsNullOrWhiteSpace(keyVaultKeyId))
        {
            if (!Uri.TryCreate(keyVaultKeyId, UriKind.Absolute, out var keyVaultUri))
            {
                throw new InvalidOperationException($"{KeyVaultKeyIdSetting} must be an absolute URI.");
            }

            dataProtection.ProtectKeysWithAzureKeyVault(keyVaultUri, new DefaultAzureCredential());
        }

        services.AddOptions<KeyManagementOptions>().Configure<IServiceProvider>((options, provider) =>
            options.XmlRepository = new RedisXmlRepository(
                () => provider.GetRequiredService<IConnectionMultiplexer>().GetDatabase(),
                DataProtectionKeyRing));

        services.AddSingleton<RedisTicketStore>();
        services.AddOptions<CookieAuthenticationOptions>(CookieAuthenticationDefaults.AuthenticationScheme)
            .PostConfigure<RedisTicketStore, TimeProvider, IOptions<GatewaySessionOptions>>(
                (cookie, store, time, session) =>
                {
                    cookie.SessionStore = store;
                    cookie.ExpireTimeSpan = session.Value.IdleTimeout;
                    cookie.SlidingExpiration = true;
                    cookie.TimeProvider = time;

                    var existing = cookie.Events.OnCheckSlidingExpiration;
                    cookie.Events.OnCheckSlidingExpiration = async context =>
                    {
                        await existing(context);
                        context.ShouldRenew = ShouldRenew(context.HttpContext.Request);
                    };
                });

        return services;
    }

    public static bool ShouldRenew(HttpRequest request) =>
        request.Path.StartsWithSegments(ApiRoutes.Prefix)
        && !(HttpMethods.IsGet(request.Method) && IsTimeLeftPath(request.Path));

    private static bool IsTimeLeftPath(PathString path) =>
        path.Equals(TimeLeftPath, StringComparison.OrdinalIgnoreCase)
        || path.Equals(TimeLeftPath + "/", StringComparison.OrdinalIgnoreCase);
}
