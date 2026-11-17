using TravelChat.Gateway.Features.Auth;
using TravelChat.Gateway.Features.Maps;
using TravelChat.Gateway.Features.Proxy;
using TravelChat.Gateway.Features.Token;
using TravelChat.ServiceDefaults;

var builder = WebApplication.CreateBuilder(args);
builder.AddServiceDefaults();

builder.Services.Configure<InternalTokenOptions>(
    builder.Configuration.GetSection(InternalTokenOptions.SectionName));
builder.Services.AddSingleton<TokenIssuer>();
builder.Services.Configure<MapsOptions>(
    builder.Configuration.GetSection(MapsOptions.SectionName));

builder.Services.AddGatewayAuth(builder.Configuration);
builder.Services
    .AddReverseProxy()
    .LoadFromConfig(builder.Configuration.GetSection("ReverseProxy"))
    .AddTokenTransform()
    .AddMapsTransform();

var app = builder.Build();
app.UseAuthentication();
app.UseAuthorization();
app.MapDefaultEndpoints();
app.MapJwksEndpoints();
app.MapAuthEndpoints();
app.MapApiFallback();
app.MapReverseProxy();
app.Run();

public partial class Program;
