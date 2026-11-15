using Microsoft.AspNetCore.Http.HttpResults;
using TravelChat.Query.Features.Shared;

namespace TravelChat.Query.Features.Search;

public static class SearchEndpoint
{
    private const int MaxQueryLength = 200;

    public static IEndpointRouteBuilder MapSearch(this IEndpointRouteBuilder app)
    {
        app.MapGet("/search/web", HandleAsync);
        return app;
    }

    private static async Task<Results<Ok<ProviderResult<IReadOnlyList<SearchResult>>>, ValidationProblem>> HandleAsync(
        string? q,
        SearchService search,
        CancellationToken ct)
    {
        var errors = new QueryErrors();
        var text = errors.RequireText(q, "q", MaxQueryLength);
        if (errors.Any || text is null)
        {
            return TypedResults.ValidationProblem(errors.Errors);
        }

        var result = await search.SearchAsync(text, ct);
        return TypedResults.Ok(result);
    }
}
