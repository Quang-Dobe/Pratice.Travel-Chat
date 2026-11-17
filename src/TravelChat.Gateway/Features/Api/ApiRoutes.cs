namespace TravelChat.Gateway.Features.Api;

public static class ApiRoutes
{
    public const string Policy = "api";

    public static readonly PathString Prefix = new("/api");

    public static IEndpointRouteBuilder MapApiFallback(this IEndpointRouteBuilder app)
    {
        app.Map("/api/{**rest}", () => TypedResults.Problem(statusCode: StatusCodes.Status404NotFound))
            .RequireAuthorization(Policy);
        return app;
    }
}
