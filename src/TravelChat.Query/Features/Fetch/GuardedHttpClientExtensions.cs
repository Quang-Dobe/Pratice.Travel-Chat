namespace TravelChat.Query.Features.Fetch;

public static class GuardedHttpClientExtensions
{
    public static IHttpClientBuilder AddGuardedClient(this IServiceCollection services, string name)
    {
        var builder = services.AddHttpClient(name);
        builder.ConfigureHttpClient(client =>
        {
            client.Timeout = GuardedHandler.Timeout;
            client.DefaultRequestHeaders.UserAgent.ParseAdd("TravelChat/1.0");
            client.DefaultRequestHeaders.Accept.ParseAdd("text/html, text/plain;q=0.9");
        });
        builder.ConfigurePrimaryHttpMessageHandler(static () => GuardedHandler.Create());
        return builder;
    }
}
