using System.Net;
using Microsoft.Extensions.DependencyInjection;
using Shouldly;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Tests;

public sealed class ProviderHttpClientTests
{
    private const string Name = "test__client";

    [Fact]
    public async Task TooManyRequestsIsNotRetried()
    {
        var ct = TestContext.Current.CancellationToken;
        var handler = new StubHandler(_ => StubHandler.Json("{}", HttpStatusCode.TooManyRequests));
        var services = new ServiceCollection();
        services.AddProviderClient(Name).ConfigurePrimaryHttpMessageHandler(() => handler);
        using var provider = services.BuildServiceProvider();
        var http = provider.GetRequiredService<IHttpClientFactory>().CreateClient(Name);

        using var response = await http.GetAsync(new Uri("https://provider.invalid/data"), ct);

        response.StatusCode.ShouldBe(HttpStatusCode.TooManyRequests);
        handler.Calls.ShouldBe(1);
    }
}
