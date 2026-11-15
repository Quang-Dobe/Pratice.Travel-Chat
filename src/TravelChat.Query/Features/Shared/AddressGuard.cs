using System.Net;
using System.Net.Sockets;

namespace TravelChat.Query.Features.Shared;

internal static class AddressGuard
{
    public static bool IsBlocked(IPAddress address)
    {
        var resolved = address.IsIPv4MappedToIPv6 ? address.MapToIPv4() : address;
        var bytes = resolved.GetAddressBytes();
        return resolved.AddressFamily switch
        {
            AddressFamily.InterNetwork => IsBlockedV4(bytes),
            AddressFamily.InterNetworkV6 => IsBlockedV6(bytes),
            _ => true,
        };
    }

    private static bool IsBlockedV4(ReadOnlySpan<byte> bytes) =>
        bytes[0] is 0 or 10 or 127
        || (bytes[0] == 100 && (bytes[1] & 0xC0) == 64)
        || (bytes[0] == 169 && bytes[1] == 254)
        || (bytes[0] == 172 && (bytes[1] & 0xF0) == 16)
        || (bytes[0] == 192 && bytes[1] == 168)
        || bytes[0] >= 224;

    private static bool IsBlockedV6(ReadOnlySpan<byte> bytes)
    {
        if (bytes[..12].IndexOfAnyExcept((byte)0) < 0)
        {
            return true;
        }

        if (bytes[0] == 0x00 && bytes[1] == 0x64 && bytes[2] == 0xFF && bytes[3] == 0x9B && bytes[4..12].IndexOfAnyExcept((byte)0) < 0)
        {
            return IsBlockedV4(bytes[12..]);
        }

        if (bytes[0] == 0x20 && bytes[1] == 0x02)
        {
            return IsBlockedV4(bytes[2..6]);
        }

        return (bytes[0] == 0x20 && bytes[1] == 0x01 && bytes[2] == 0 && bytes[3] == 0)
            || (bytes[0] & 0xFE) == 0xFC
            || (bytes[0] == 0xFE && (bytes[1] & 0xC0) is 0x80 or 0xC0)
            || bytes[0] == 0xFF;
    }
}
