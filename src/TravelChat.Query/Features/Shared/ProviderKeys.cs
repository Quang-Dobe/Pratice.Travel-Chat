namespace TravelChat.Query.Features.Shared;

public sealed class ProviderKeys(IConfiguration configuration)
{
    public string? ForClient(string client) =>
        Read($"ProviderKeys:{client.Replace("__", ":", StringComparison.Ordinal)}");

    private string? Read(string path)
    {
        var value = configuration[path];
        return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
    }
}
