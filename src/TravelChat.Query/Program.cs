using TravelChat.Query.Features.Flights;
using TravelChat.Query.Features.Hotels;
using TravelChat.Query.Features.Places;
using TravelChat.Query.Features.Shared;
using TravelChat.Query.Features.Weather;
using TravelChat.ServiceDefaults;

var builder = WebApplication.CreateBuilder(args);
builder.AddServiceDefaults();
builder.Logging.AddFilter("System.Net.Http.HttpClient", LogLevel.Warning);

var redis = builder.Configuration.GetConnectionString("Redis");
if (!string.IsNullOrWhiteSpace(redis))
{
    builder.Services.AddStackExchangeRedisCache(options => options.Configuration = redis);
}

builder.Services.AddHybridCache();
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton<ProviderKeys>();
builder.Services.AddSingleton<CachedFetch>();

builder.Services.AddProviderClient(WeatherService.ClientName);
builder.Services.AddSingleton<WeatherService>();

builder.Services.AddProviderClient(PlacesService.ClientName);
builder.Services.AddSingleton<PlacesService>();

builder.Services.AddProviderClient(FlightsService.ClientName);
builder.Services.AddSingleton<FlightsService>();

builder.Services.AddProviderClient(HotelsService.ClientName);
builder.Services.AddSingleton<HotelsService>();

var app = builder.Build();
app.MapDefaultEndpoints();
app.MapWeather();
app.MapPlaces();
app.MapFlights();
app.MapHotels();
app.Run();

public partial class Program;
