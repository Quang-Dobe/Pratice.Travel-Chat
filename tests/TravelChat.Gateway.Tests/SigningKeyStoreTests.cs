using System.Security.Cryptography;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.Extensions.Options;
using Shouldly;
using TravelChat.Gateway.Features.Token;

namespace TravelChat.Gateway.Tests;

public sealed class SigningKeyStoreTests
{
    private sealed class RaceLostSlot(InMemorySigningKeySlot inner, byte[] winner) : ISigningKeySlot
    {
        private int _reads;

        public byte[]? Read() => _reads++ == 0 ? null : inner.Read();

        public bool TryCreate(byte[] bytes)
        {
            inner.TryCreate(winner);
            return false;
        }
    }

    private static TokenIssuer IssuerOver(RSA key) =>
        new(Options.Create(new InternalTokenOptions()), key);

    private static bool SamePublicKey(RSA first, RSA second)
    {
        var a = first.ExportParameters(includePrivateParameters: false);
        var b = second.ExportParameters(includePrivateParameters: false);
        return a.Modulus!.SequenceEqual(b.Modulus!) && a.Exponent!.SequenceEqual(b.Exponent!);
    }

    [Fact]
    public void LoadOrCreateOnEmptySlotCreatesAndStoresOneKey()
    {
        var slot = new InMemorySigningKeySlot();
        var store = new SigningKeyStore(slot, new EphemeralDataProtectionProvider());

        using var key = store.LoadOrCreate();

        key.KeySize.ShouldBe(2048);
        slot.Read().ShouldNotBeNull();
    }

    [Fact]
    public void LoadOrCreateOnSecondStoreOverSameSlotReturnsSameKeyAndKid()
    {
        var slot = new InMemorySigningKeySlot();
        var provider = new EphemeralDataProtectionProvider();

        using var first = new SigningKeyStore(slot, provider).LoadOrCreate();
        using var second = new SigningKeyStore(slot, provider).LoadOrCreate();

        SamePublicKey(first, second).ShouldBeTrue();
        using var firstIssuer = IssuerOver(first);
        using var secondIssuer = IssuerOver(second);
        secondIssuer.PublicJwk.Kid.ShouldBe(firstIssuer.PublicJwk.Kid);
    }

    [Fact]
    public void LoadOrCreateAfterLostRaceReturnsStoredKeyAndDiscardsOwn()
    {
        var provider = new EphemeralDataProtectionProvider();
        var winnerSlot = new InMemorySigningKeySlot();
        using var winnerKey = new SigningKeyStore(winnerSlot, provider).LoadOrCreate();
        var winnerBytes = winnerSlot.Read()!;

        var shared = new InMemorySigningKeySlot();
        var store = new SigningKeyStore(new RaceLostSlot(shared, winnerBytes), provider);

        using var key = store.LoadOrCreate();

        SamePublicKey(key, winnerKey).ShouldBeTrue();
        shared.Read()!.SequenceEqual(winnerBytes).ShouldBeTrue();
    }

    [Fact]
    public void LoadOrCreateStoresBytesThatAreNotThePlainPkcs8Key()
    {
        var slot = new InMemorySigningKeySlot();
        using var key = new SigningKeyStore(slot, new EphemeralDataProtectionProvider()).LoadOrCreate();

        slot.Read()!.SequenceEqual(key.ExportPkcs8PrivateKey()).ShouldBeFalse();
    }

    [Fact]
    public void LoadOrCreateOnSlotWrittenUnderAnotherProviderThrowsAndLeavesSlotUntouched()
    {
        var slot = new InMemorySigningKeySlot();
        using var original = new SigningKeyStore(slot, new EphemeralDataProtectionProvider()).LoadOrCreate();
        var before = slot.Read()!;

        var other = new SigningKeyStore(slot, new EphemeralDataProtectionProvider());

        Should.Throw<CryptographicException>(() => other.LoadOrCreate());
        slot.Read()!.SequenceEqual(before).ShouldBeTrue();
    }
}
