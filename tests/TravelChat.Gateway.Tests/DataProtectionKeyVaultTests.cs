using Microsoft.AspNetCore.DataProtection.KeyManagement;
using Microsoft.AspNetCore.DataProtection.StackExchangeRedis;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;
using Shouldly;
using StackExchange.Redis;
using TravelChat.Gateway.Features.Session;

namespace TravelChat.Gateway.Tests;

public sealed class DataProtectionKeyVaultTests
{
    private const string KeyId = "https://test-vault.vault.azure.net/keys/dp/1";

    private static ServiceCollection Register(string? keyId)
    {
        var values = new Dictionary<string, string?> { ["ConnectionStrings:Redis"] = "localhost:6379" };
        if (keyId is not null)
        {
            values["DataProtection:KeyVaultKeyId"] = keyId;
        }

        var configuration = new ConfigurationBuilder().AddInMemoryCollection(values).Build();
        var services = new ServiceCollection();
        services.AddLogging();
        services.AddGatewaySessions(configuration);
        services.Replace(ServiceDescriptor.Singleton<IConnectionMultiplexer>(
            _ => throw new InvalidOperationException("Redis must not be used in Gateway tests.")));
        return services;
    }

    private static KeyManagementOptions Resolve(ServiceCollection services)
    {
        using var provider = services.BuildServiceProvider();
        return provider.GetRequiredService<IOptions<KeyManagementOptions>>().Value;
    }

    [Fact]
    public void KeyIdSetWrapsKeysAndKeepsRedisRepository()
    {
        var options = Resolve(Register(KeyId));

        options.XmlEncryptor.ShouldNotBeNull();
        options.XmlRepository.ShouldBeOfType<RedisXmlRepository>();
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void KeyIdUnsetOrBlankLeavesKeysUnwrapped(string? keyId)
    {
        var options = Resolve(Register(keyId));

        options.XmlEncryptor.ShouldBeNull();
        options.XmlRepository.ShouldBeOfType<RedisXmlRepository>();
    }

    [Fact]
    public void KeyIdNotAUriThrows()
    {
        var exception = Should.Throw<InvalidOperationException>(() => Register("not a uri"));

        exception.Message.ShouldContain("DataProtection:KeyVaultKeyId");
    }
}
