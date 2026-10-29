namespace TravelChat.Query.Features.Shared;

public sealed class ProviderFailureException : Exception
{
    public ProviderFailureException()
        : this(ProviderStatus.Error, "Provider call failed.")
    {
    }

    public ProviderFailureException(string message)
        : this(ProviderStatus.Error, message)
    {
    }

    public ProviderFailureException(string message, Exception innerException)
        : base(message, innerException)
    {
        Status = ProviderStatus.Error;
    }

    public ProviderFailureException(string status, string message)
        : base(message)
    {
        Status = status;
    }

    public string Status { get; }

    public ProviderResult<T> ToResult<T>() => new(Status, Message, default);
}
