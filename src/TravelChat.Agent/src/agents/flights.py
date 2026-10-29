from src.agents.base import AgentSpec, ToolSpec

SPEC = AgentSpec(
    domain="flights",
    name="flights_agent",
    description="Searches round-trip flights for a trip.",
    when_to_use=(
        "the plan needs flight options: the first plan, or a change to the dates, destination, "
        "travellers, origin, or flight wishes."
    ),
    how_to_use=(
        "Send one request naming the origin city, destination city, departure date, return date, "
        "travellers, and any wishes such as nonstop."
    ),
    tools=(
        ToolSpec(
            "flights__search",
            "Round-trip options with total prices in USD. Origin and destination are 3-letter "
            "IATA airport codes, so convert city names yourself. Dates are ISO yyyy-MM-dd; "
            "travellers 1 to 9.",
        ),
    ),
)
