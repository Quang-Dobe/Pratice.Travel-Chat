using System.Text.Json;

namespace TravelChat.Query.Features.Shared;

public static class JsonReading
{
    public static JsonElement Require(this JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out var value)
            ? value
            : throw Unexpected();

    public static double RequireNumber(this JsonElement element, string name)
    {
        var value = element.Require(name);
        return value.ValueKind == JsonValueKind.Number ? value.GetDouble() : throw Unexpected();
    }

    public static string? OptionalString(this JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    public static double? NumberAt(this JsonElement array, int index)
    {
        if (array.ValueKind != JsonValueKind.Array || index >= array.GetArrayLength())
        {
            return null;
        }

        var item = array[index];
        return item.ValueKind == JsonValueKind.Number ? item.GetDouble() : null;
    }

    private static ProviderFailureException Unexpected() => new(ProviderStatus.Error, "unexpected response from provider");
}
