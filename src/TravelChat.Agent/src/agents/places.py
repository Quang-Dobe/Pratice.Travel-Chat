from src.agents.base import AgentSpec, ToolSpec

SPEC = AgentSpec(
    domain="places",
    name="places_agent",
    description="Finds attractions in a city.",
    when_to_use="the user asks what to see or do, or the itinerary needs activities.",
    how_to_use="Send one request naming the city and the interests, such as food or hiking.",
    tools=(
        ToolSpec(
            "places__attractions",
            "Up to 20 attractions in a city, each with a name and a category, optionally "
            "narrowed by interests.",
        ),
    ),
)
