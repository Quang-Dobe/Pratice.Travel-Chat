using TravelChat.Gateway.Features.Api;
using TravelChat.Gateway.Features.Auth;
using TravelChat.Gateway.Features.Maps;
using TravelChat.Gateway.Features.Proxy;
using TravelChat.Gateway.Features.Token;
using TravelChat.ServiceDefaults;

var builder = WebApplication.CreateBuilder(args);
builder.AddServiceDefaults();

builder.Services.AddTokenIssuer();
builder.Services.AddGatewayAuth(builder.Configuration);
builder.Services
    .AddReverseProxy()
    .LoadFromConfig(builder.Configuration.GetSection("ReverseProxy"))
    .AddTokenTransform()
    .AddMapsProxy();

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
