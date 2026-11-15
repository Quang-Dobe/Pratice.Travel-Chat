using Microsoft.AspNetCore.Http.HttpResults;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Fetch;

public static class FetchEndpoint
{
    private const int MaxUrlLength = 2000;

    public static IEndpointRouteBuilder MapFetch(this IEndpointRouteBuilder app)
    {
        app.MapGet("/fetch/page", HandleAsync);
        return app;
    }

    private static async Task<Results<Ok<ProviderResult<PageText>>, ValidationProblem>> HandleAsync(
        string? url,
        FetchService fetch,
        CancellationToken ct)
    {
        var errors = new QueryErrors();
        var text = errors.RequireText(url, "url", MaxUrlLength);
        if (errors.Any || text is null)
        {
            return TypedResults.ValidationProblem(errors.Errors);
        }

        if (FetchService.ParseHttps(text) is not { } uri)
        {
            return TypedResults.Ok(ProviderResult.Error<PageText>("page could not be read"));
        }

        var result = await fetch.GetPageAsync(uri, ct);
        return TypedResults.Ok(result);
    }
}
