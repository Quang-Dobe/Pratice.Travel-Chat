using Polly;
using Polly.Timeout;

namespace TravelChat.Query.Features.Shared;

public static class ProviderHttpClientExtensions
{
    public static IHttpClientBuilder AddProviderClient(this IServiceCollection services, string name)
    {
        var builder = services.AddHttpClient(name);
        builder.AddStandardResilienceHandler(options =>
            options.Retry.ShouldHandle = args => ValueTask.FromResult(ShouldRetry(args.Outcome)));
        return builder;
    }

    private static bool ShouldRetry(Outcome<HttpResponseMessage> outcome)
    {
        if (outcome.Exception is not null)
        {
            return outcome.Exception is HttpRequestException or TimeoutRejectedException;
        }

        return outcome.Result is { } response
            && ((int)response.StatusCode >= 500 || (int)response.StatusCode == 408);
    }
}
