namespace TravelChat.Query.Features.Flights;

public sealed record Airport(string Code, string Name, double? Lat, double? Lon);

public sealed record FlightLeg(Airport From, Airport To, string Departs, string Arrives);

public sealed record FlightOption(
    string Id,
    string Summary,
    decimal PriceUsd,
    string DeepLink,
    IReadOnlyList<FlightLeg> Legs);
