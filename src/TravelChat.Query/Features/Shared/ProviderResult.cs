namespace TravelChat.Query.Features.Shared;

public static class ProviderStatus
{
    public const string Ok = "ok";
    public const string Unavailable = "unavailable";
    public const string Limit = "limit";
    public const string Error = "error";
}

public sealed record ProviderResult<T>(string Status, string Message, T? Data);

public static class ProviderResult
{
    public static ProviderResult<T> Ok<T>(T data) => new(ProviderStatus.Ok, string.Empty, data);

    public static ProviderResult<T> Unavailable<T>(string message) => new(ProviderStatus.Unavailable, message, default);

    public static ProviderResult<T> Limit<T>(string message) => new(ProviderStatus.Limit, message, default);

    public static ProviderResult<T> Error<T>(string message) => new(ProviderStatus.Error, message, default);
}
