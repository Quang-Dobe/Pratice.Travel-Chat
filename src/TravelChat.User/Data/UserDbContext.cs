using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace TravelChat.User.Data;

public sealed class AppUser
{
    public Guid Id { get; set; }

    public required string Oid { get; set; }

    public required string Name { get; set; }

    public required string Email { get; set; }

    public bool IsActive { get; set; }
}

public sealed class AppUserConfiguration : IEntityTypeConfiguration<AppUser>
{
    public void Configure(EntityTypeBuilder<AppUser> builder)
    {
        builder.ToTable("users");
        builder.HasKey(u => u.Id).HasName("pk_users");
        builder.Property(u => u.Id).HasColumnName("id").ValueGeneratedNever();
        builder.Property(u => u.Oid).HasColumnName("oid").IsRequired();
        builder.Property(u => u.Name).HasColumnName("name").IsRequired();
        builder.Property(u => u.Email).HasColumnName("email").IsRequired();
        builder.Property(u => u.IsActive).HasColumnName("is_active");
        builder.HasIndex(u => u.Oid).IsUnique().HasDatabaseName("ix_users_oid");
    }
}

public sealed class UserDbContext(DbContextOptions<UserDbContext> options) : DbContext(options)
{
    public DbSet<AppUser> Users => Set<AppUser>();

    protected override void OnModelCreating(ModelBuilder modelBuilder) =>
        modelBuilder.ApplyConfigurationsFromAssembly(typeof(UserDbContext).Assembly);
}
