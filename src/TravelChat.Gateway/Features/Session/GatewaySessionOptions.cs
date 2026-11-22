namespace TravelChat.Gateway.Features.Session;

public sealed class GatewaySessionOptions
{
    public const string SectionName = "Session";

    public TimeSpan IdleTimeout { get; set; } = TimeSpan.FromMinutes(30);

    public TimeSpan AbsoluteLimit { get; set; } = TimeSpan.FromHours(8);

    public static bool IsValid(GatewaySessionOptions options) =>
        options.IdleTimeout > TimeSpan.Zero
        && options.AbsoluteLimit > TimeSpan.Zero
        && options.AbsoluteLimit >= options.IdleTimeout;
}
