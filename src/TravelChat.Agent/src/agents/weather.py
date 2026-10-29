from src.agents.base import AgentSpec, ToolSpec

SPEC = AgentSpec(
    domain="weather",
    name="weather_agent",
    description="Looks up the daily weather forecast for a city.",
    when_to_use=(
        "the user asks about the weather, or itinerary days fall within the forecast range."
    ),
    how_to_use="Send one request naming the city and the first and last dates.",
    tools=(
        ToolSpec(
            "weather__forecast",
            "Daily forecast for a city and a date range. Dates are ISO yyyy-MM-dd; temperatures "
            "in degrees Celsius, rain chance in percent.",
        ),
    ),
)
