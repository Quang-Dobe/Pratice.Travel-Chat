using System.Globalization;

namespace TravelChat.Query.Features.Shared;

public sealed class QueryErrors
{
    private readonly Dictionary<string, string[]> _errors = [];

    public bool Any => _errors.Count > 0;

    public IDictionary<string, string[]> Errors => _errors;

    public void Add(string name, string message) => _errors[name] = [message];

    public string? RequireText(string? value, string name, int maxLength)
    {
        var text = value?.Trim();
        if (string.IsNullOrEmpty(text))
        {
            Add(name, $"{name} is required.");
            return null;
        }

        if (text.Length > maxLength)
        {
            Add(name, $"{name} must be at most {maxLength.ToString(CultureInfo.InvariantCulture)} characters.");
            return null;
        }

        return text;
    }

    public DateOnly? RequireDate(string? value, string name)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            Add(name, $"{name} is required.");
            return null;
        }

        if (DateOnly.TryParseExact(value.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var date))
        {
            return date;
        }

        Add(name, $"{name} must be a date in yyyy-MM-dd format.");
        return null;
    }
}
