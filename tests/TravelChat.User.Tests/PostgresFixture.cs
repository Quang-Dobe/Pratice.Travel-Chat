using Microsoft.EntityFrameworkCore;
using Testcontainers.PostgreSql;
using TravelChat.User.Data;

namespace TravelChat.User.Tests;

public sealed class PostgresFixture : IAsyncLifetime
{
    private readonly PostgreSqlContainer _container = new PostgreSqlBuilder("postgres:17").Build();

    public string ConnectionString => _container.GetConnectionString();

    public async ValueTask InitializeAsync()
    {
        await _container.StartAsync(TestContext.Current.CancellationToken);
        await using var db = CreateContext();
        await db.Database.MigrateAsync(TestContext.Current.CancellationToken);
    }

    public UserDbContext CreateContext() =>
        new(new DbContextOptionsBuilder<UserDbContext>().UseNpgsql(ConnectionString).Options);

    public async ValueTask DisposeAsync() => await _container.DisposeAsync();
}

[CollectionDefinition("postgres")]
public sealed class PostgresDefinition : ICollectionFixture<PostgresFixture>;
