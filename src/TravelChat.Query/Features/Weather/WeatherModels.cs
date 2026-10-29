namespace TravelChat.Query.Features.Weather;

public sealed record ForecastDay(string Date, double? HighC, double? LowC, int? RainChancePct);

public sealed record Forecast(string City, IReadOnlyList<ForecastDay> Days);
