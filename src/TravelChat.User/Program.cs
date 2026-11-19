using Microsoft.EntityFrameworkCore;
using TravelChat.ServiceDefaults;
using TravelChat.User.Data;
using TravelChat.User.Features.Me;
using TravelChat.User.Features.Seed;

var builder = WebApplication.CreateBuilder(args);
builder.AddServiceDefaults();
builder.AddInternalTokenAuth();
builder.Services.AddDbContext<UserDbContext>((sp, options) =>
    options.UseNpgsql(sp.GetRequiredService<IConfiguration>().GetConnectionString("Users")));
builder.Services.AddScoped<UserSeeder>();

var app = builder.Build();

using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<UserDbContext>();
    await db.Database.MigrateAsync();
    await scope.ServiceProvider.GetRequiredService<UserSeeder>().SyncAsync(CancellationToken.None);
}

app.UseAuthentication();
app.UseAuthorization();
app.MapDefaultEndpoints();
app.MapMe();
app.Run();

public partial class Program;
