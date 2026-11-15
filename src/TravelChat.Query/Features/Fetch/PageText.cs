using System.Diagnostics.CodeAnalysis;

namespace TravelChat.Query.Features.Fetch;

[SuppressMessage("Design", "CA1056:URI-like properties should not be strings", Justification = "Serialised as a plain string in the JSON contract.")]
public sealed record PageText(string Title, string Url, string Text);
