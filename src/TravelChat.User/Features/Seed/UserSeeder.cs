using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TravelChat.User.Data;

namespace TravelChat.User.Features.Seed;

public sealed class UserSeeder(UserDbContext db, IConfiguration configuration)
{
    private static readonly JsonSerializerOptions s_json = new(JsonSerializerDefaults.Web);

    public async Task SyncAsync(CancellationToken ct)
    {
        var entries = await ReadEntriesAsync(ct);
        var oids = entries.Select(e => e.Oid).ToList();

        await using var transaction = await db.Database.BeginTransactionAsync(ct);

        var existing = await db.Users
            .Where(u => oids.Contains(u.Oid))
            .ToDictionaryAsync(u => u.Oid, StringComparer.Ordinal, ct);

        foreach (var entry in entries)
        {
            if (existing.TryGetValue(entry.Oid, out var row))
            {
                row.Name = entry.Name;
                row.Email = entry.Email;
                row.IsActive = true;
            }
            else
            {
                db.Users.Add(new AppUser
                {
                    Id = Guid.NewGuid(),
                    Oid = entry.Oid,
                    Name = entry.Name,
                    Email = entry.Email,
                    IsActive = true,
                });
            }
        }

        await db.SaveChangesAsync(ct);

        await db.Users
            .Where(u => !oids.Contains(u.Oid) && u.IsActive)
            .ExecuteUpdateAsync(s => s.SetProperty(u => u.IsActive, false), ct);

        await transaction.CommitAsync(ct);
    }

    private async Task<List<ValidEntry>> ReadEntriesAsync(CancellationToken ct)
    {
        var path = configuration["Seed:UsersFile"];
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new InvalidOperationException("Seed:UsersFile is not configured.");
        }

        var json = await File.ReadAllTextAsync(path, ct);
        var entries = JsonSerializer.Deserialize<List<SeedEntry?>>(json, s_json)
            ?? throw new InvalidOperationException("The users file must hold a JSON array.");

        var seen = new HashSet<string>(StringComparer.Ordinal);
        var valid = new List<ValidEntry>(entries.Count);
        foreach (var entry in entries)
        {
            if (entry is not { Oid: { } oid, Name: { } name, Email: { } email }
                || string.IsNullOrWhiteSpace(oid)
                || string.IsNullOrWhiteSpace(name)
                || string.IsNullOrWhiteSpace(email))
            {
                throw new InvalidOperationException("Every user needs a non-empty oid, name and email.");
            }

            if (!seen.Add(oid))
            {
                throw new InvalidOperationException($"Duplicate oid in the users file: {oid}");
            }

            valid.Add(new ValidEntry(oid, name, email));
        }

        return valid;
    }

    public sealed record SeedEntry(string? Oid, string? Name, string? Email);

    private sealed record ValidEntry(string Oid, string Name, string Email);
}
