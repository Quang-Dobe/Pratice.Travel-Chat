namespace TravelChat.Query.Features.Hotels;

public sealed record HotelLocation(double Lat, double Lon);

public sealed record HotelOption(string Id, string Summary, decimal PriceUsd, string DeepLink, HotelLocation? Location);
