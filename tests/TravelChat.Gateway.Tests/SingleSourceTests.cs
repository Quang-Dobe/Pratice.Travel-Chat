using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Shouldly;
using TravelChat.Gateway.Features.Auth;
using TravelChat.Gateway.Features.Token;
using TravelChat.ServiceDefaults;

namespace TravelChat.Gateway.Tests;

public sealed class SingleSourceTests
{
    [Fact]
    public async Task ServiceDefaultsLeaveSerilogAsTheOnlyConsoleWriter()
    {
        var builder = WebApplication.CreateBuilder();
        builder.AddServiceDefaults();
        await using var app = builder.Build();

        var providers = app.Services.GetServices<ILoggerProvider>().Select(provider => provider.GetType().Name);

        providers.ShouldNotContain("ConsoleLoggerProvider");
    }

    [Fact]
    public void InviteCheckReadsTheUserServiceAddressFromTheProxyCluster()
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["ReverseProxy:Clusters:q-user:Destinations:primary:Address"] = "http://q-user-test:9090",
            })
            .Build();

        InviteCheckRegistration.UserServiceAddress(configuration).ShouldBe(new Uri("http://q-user-test:9090"));
    }

    [Fact]
    public void IssuedTokenUsesTheSharedIssuerAndAudience()
    {
        var options = new InternalTokenOptions();

        options.Issuer.ShouldBe(InternalToken.Issuer);
        options.Audience.ShouldBe(InternalToken.Audience);
    }
}
