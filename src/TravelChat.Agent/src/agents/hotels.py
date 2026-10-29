from src.agents.base import AgentSpec, ToolSpec

SPEC = AgentSpec(
    domain="hotels",
    name="hotels_agent",
    description="Searches hotels for a stay.",
    when_to_use=(
        "the plan needs hotel options: the first plan, or a change to the dates, destination, "
        "travellers, or hotel wishes."
    ),
    how_to_use=(
        "Send one request naming the city, check-in date, check-out date, travellers, and any "
        "wishes such as cheaper or near the station."
    ),
    tools=(
        ToolSpec(
            "hotels__search",
            "Hotel options in a city for a stay, with total prices in USD for the whole stay. "
            "Dates are ISO yyyy-MM-dd; travellers 1 to 9.",
        ),
    ),
)
