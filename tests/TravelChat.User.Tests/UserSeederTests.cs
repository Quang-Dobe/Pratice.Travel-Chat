using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Shouldly;
using Testcontainers.PostgreSql;
using TravelChat.User.Data;
using TravelChat.User.Features.Seed;
using Xunit;

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

[Collection("postgres")]
public sealed class UserSeederTests(PostgresFixture postgres) : IAsyncLifetime, IDisposable
{
    private const string OidOne = "00000000-0000-0000-0000-000000000001";
    private const string OidTwo = "00000000-0000-0000-0000-000000000002";

    private readonly List<string> _files = [];

    public async ValueTask InitializeAsync()
    {
        await using var db = postgres.CreateContext();
        await db.Users.ExecuteDeleteAsync(TestContext.Current.CancellationToken);
    }

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;

    public void Dispose()
    {
        foreach (var file in _files)
        {
            File.Delete(file);
        }
    }

    [Fact]
    public async Task FirstSyncInsertsBothEntriesAsActive()
    {
        await SyncAsync(Json((OidOne, "One", "one@example.invalid"), (OidTwo, "Two", "two@example.invalid")));

        var rows = await ReadUsersAsync();
        rows.Count.ShouldBe(2);
        rows.ShouldAllBe(u => u.IsActive);
        rows.Select(u => u.Oid).ShouldBe([OidOne, OidTwo], ignoreOrder: true);
    }

    [Fact]
    public async Task SecondSyncWithEntryRemovedDeactivatesItAndKeepsIds()
    {
        await SyncAsync(Json((OidOne, "One", "one@example.invalid"), (OidTwo, "Two", "two@example.invalid")));
        var before = (await ReadUsersAsync()).ToDictionary(u => u.Oid, u => u.Id);

        await SyncAsync(Json((OidOne, "One", "one@example.invalid")));

        var after = await ReadUsersAsync();
        after.Count.ShouldBe(2);
        after.Single(u => u.Oid == OidOne).IsActive.ShouldBeTrue();
        after.Single(u => u.Oid == OidTwo).IsActive.ShouldBeFalse();
        after.ShouldAllBe(u => u.Id == before[u.Oid]);
    }

    [Fact]
    public async Task ReAddedEntryReactivatesTheSameId()
    {
        await SyncAsync(Json((OidOne, "One", "one@example.invalid"), (OidTwo, "Two", "two@example.invalid")));
        var originalId = (await ReadUsersAsync()).Single(u => u.Oid == OidTwo).Id;
        await SyncAsync(Json((OidOne, "One", "one@example.invalid")));

        await SyncAsync(Json((OidOne, "One", "one@example.invalid"), (OidTwo, "Two", "two@example.invalid")));

        var row = (await ReadUsersAsync()).Single(u => u.Oid == OidTwo);
        row.IsActive.ShouldBeTrue();
        row.Id.ShouldBe(originalId);
    }

    [Fact]
    public async Task ChangedNameAndEmailUpdateInPlace()
    {
        await SyncAsync(Json((OidOne, "One", "one@example.invalid")));
        var originalId = (await ReadUsersAsync()).Single().Id;

        await SyncAsync(Json((OidOne, "Renamed", "renamed@example.invalid")));

        var row = (await ReadUsersAsync()).Single();
        row.Id.ShouldBe(originalId);
        row.Name.ShouldBe("Renamed");
        row.Email.ShouldBe("renamed@example.invalid");
    }

    [Theory]
    [InlineData("not json")]
    [InlineData("{}")]
    public async Task MalformedFileThrowsJsonException(string content)
    {
        var path = WriteFile(content);

        await Assert.ThrowsAsync<JsonException>(() => SyncFileAsync(path));
    }

    [Theory]
    [InlineData("""[{"name":"A","email":"a@x"}]""")]
    [InlineData("""[{"oid":"a","name":"A"}]""")]
    [InlineData("""[{"oid":"a","name":"A","email":""}]""")]
    [InlineData("""[{"oid":"a","name":"A","email":"a@x"},{"oid":"a","name":"B","email":"b@x"}]""")]
    public async Task InvalidEntriesThrowInvalidOperationException(string content)
    {
        var path = WriteFile(content);

        await Assert.ThrowsAsync<InvalidOperationException>(() => SyncFileAsync(path));
    }

    private static string Json(params (string Oid, string Name, string Email)[] entries) =>
        "[" + string.Join(",", entries.Select(e => $$"""{"oid":"{{e.Oid}}","name":"{{e.Name}}","email":"{{e.Email}}"}""")) + "]";

    private string WriteFile(string content)
    {
        var path = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName() + ".json");
        File.WriteAllText(path, content);
        _files.Add(path);
        return path;
    }

    private Task SyncAsync(string json) => SyncFileAsync(WriteFile(json));

    private async Task SyncFileAsync(string path)
    {
        await using var db = postgres.CreateContext();
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["Seed:UsersFile"] = path })
            .Build();
        await new UserSeeder(db, configuration).SyncAsync(TestContext.Current.CancellationToken);
    }

    private async Task<List<AppUser>> ReadUsersAsync()
    {
        await using var db = postgres.CreateContext();
        return await db.Users.AsNoTracking().ToListAsync(TestContext.Current.CancellationToken);
    }
}
