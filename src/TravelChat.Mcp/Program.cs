using TravelChat.Mcp.Features.Tools;
using TravelChat.ServiceDefaults;

var builder = WebApplication.CreateBuilder(args);
builder.AddServiceDefaults();

var queryBaseUrl = builder.Configuration["Query:BaseUrl"]
    ?? throw new InvalidOperationException("Query:BaseUrl is not configured.");
builder.Services.AddHttpClient<QueryClient>(client => client.BaseAddress = new Uri(queryBaseUrl));

builder.Services
    .AddMcpServer()
    .WithHttpTransport(options => options.Stateless = true)
    .WithTools<TravelTools>();

var app = builder.Build();
app.MapDefaultEndpoints();
app.MapMcp("/mcp");
app.Run();
