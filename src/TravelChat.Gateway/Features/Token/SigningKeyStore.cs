using System.Security.Cryptography;
using Microsoft.AspNetCore.DataProtection;
using StackExchange.Redis;

namespace TravelChat.Gateway.Features.Token;

public interface ISigningKeySlot
{
    byte[]? Read();

    bool TryCreate(byte[] bytes);
}

public sealed class RedisSigningKeySlot(IConnectionMultiplexer redis) : ISigningKeySlot
{
    public const string RedisKey = "gateway:signing-key";

    public byte[]? Read()
    {
        var value = redis.GetDatabase().StringGet(RedisKey);
        return value.HasValue ? (byte[]?)value : null;
    }

    public bool TryCreate(byte[] bytes) =>
        redis.GetDatabase().StringSet(RedisKey, bytes, expiry: null, when: When.NotExists);
}

public sealed class SigningKeyStore(ISigningKeySlot slot, IDataProtectionProvider provider)
{
    public const string ProtectorPurpose = "TravelChat.Gateway.SigningKey";

    private const int KeySizeBits = 2048;

    private readonly IDataProtector _protector = provider.CreateProtector(ProtectorPurpose);

    public RSA LoadOrCreate()
    {
        var stored = slot.Read();
        if (stored is not null)
        {
            return Import(stored);
        }

        var key = RSA.Create(KeySizeBits);
        try
        {
            if (slot.TryCreate(_protector.Protect(key.ExportPkcs8PrivateKey())))
            {
                return key;
            }
        }
        catch
        {
            key.Dispose();
            throw;
        }

        key.Dispose();
        stored = slot.Read()
            ?? throw new InvalidOperationException("The signing key slot was filled by another start but cannot be read.");
        return Import(stored);
    }

    private RSA Import(byte[] stored)
    {
        var pkcs8 = _protector.Unprotect(stored);
        var key = RSA.Create();
        try
        {
            key.ImportPkcs8PrivateKey(pkcs8, out _);
            return key;
        }
        catch
        {
            key.Dispose();
            throw;
        }
    }
}
