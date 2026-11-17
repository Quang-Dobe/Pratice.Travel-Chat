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

    public static JsonElement? FirstResult(this JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object)
        {
            throw Unexpected();
        }

        return root.TryGetProperty("results", out var results)
            && results.ValueKind == JsonValueKind.Array
            && results.GetArrayLength() > 0
                ? results[0]
                : null;
    }

    public static bool IsLatLon(double lat, double lon) => lat is >= -90 and <= 90 && lon is >= -180 and <= 180;

    public static (double Lat, double Lon)? LatLon(this JsonElement element, string latName, string lonName)
    {
        if (element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(latName, out var latitude)
            && latitude.ValueKind == JsonValueKind.Number
            && element.TryGetProperty(lonName, out var longitude)
            && longitude.ValueKind == JsonValueKind.Number)
        {
            var lat = latitude.GetDouble();
            var lon = longitude.GetDouble();
            if (IsLatLon(lat, lon))
            {
                return (lat, lon);
            }
        }

        return null;
    }

    private static ProviderFailureException Unexpected() => new(ProviderStatus.Error, "unexpected response from provider");
}
