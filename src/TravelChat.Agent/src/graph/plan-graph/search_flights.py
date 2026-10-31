import copy
from typing import Any

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.utils.messages import parse_options, tool_text
from src.utils.tools import tool_uses
from src.utils.trip import DEFAULT_TRAVELLERS, Brief, Criteria, Trip, budget_hint, prefer_new

MAX_OPTIONS = 3
TOOL = "flights__search"


def build_request(trip: Trip, focused: bool = False) -> str:
    brief = trip.get("brief") or Brief()
    travellers = brief.get("travellers", DEFAULT_TRAVELLERS)
    request = (
        f"Search round-trip flights from {brief.get('origin', '')} "
        f"to {brief.get('destination', '')}, departing {brief.get('start_date', '')} "
        f"and returning {brief.get('end_date', '')}, for {travellers} traveller(s). "
        "Use 3-letter IATA codes."
    )
    hint = budget_hint(brief)
    if hint:
        request = f"{request} {hint}"
    if hint and focused:
        request = f"{request} The user wants the flight to reflect this budget."
    wanted = trip.get("criteria", Criteria()).get("flights")
    return f"{request} The user also asks for: {wanted}." if wanted else request


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    trip = copy.deepcopy(state.get("trip") or Trip())
    result = await deps.agents["flights"].run(build_request(trip, state.get("focus") == "flights"))
    options = parse_options(tool_text(result.calls, TOOL))
    cards = state.get("cards") or []
    asked = (
        state.get("change") == "flights"
        or state.get("focus") == "flights"
        or (state.get("details") or {}).get("flight_criteria")
    )
    if asked and cards:
        options = prefer_new(options, [option["id"] for option in cards[-1]["flights"]])
    trip["flights"] = options[:MAX_OPTIONS]
    return {"trip": trip, "calls": [*state.get("calls", []), *tool_uses(result.calls)]}
