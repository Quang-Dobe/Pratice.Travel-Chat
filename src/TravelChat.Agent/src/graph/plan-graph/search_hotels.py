import copy
from typing import Any

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.utils.messages import parse_options, tool_text
from src.utils.tools import tool_uses
from src.utils.trip import DEFAULT_TRAVELLERS, Brief, Criteria, Trip, prefer_new

MAX_OPTIONS = 3
TOOL = "hotels__search"


def build_request(trip: Trip) -> str:
    brief = trip.get("brief") or Brief()
    travellers = brief.get("travellers", DEFAULT_TRAVELLERS)
    request = (
        f"Search hotels in {brief.get('destination', '')}, "
        f"check-in {brief.get('start_date', '')}, check-out {brief.get('end_date', '')}, "
        f"for {travellers} traveller(s)."
    )
    wanted = trip.get("criteria", Criteria()).get("hotels")
    return f"{request} The user also asks for: {wanted}." if wanted else request


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    trip = copy.deepcopy(state.get("trip") or Trip())
    result = await deps.agents["hotels"].run(build_request(trip))
    options = parse_options(tool_text(result.calls, TOOL))
    cards = state.get("cards") or []
    asked = state.get("change") == "hotel" or (state.get("details") or {}).get("hotel_criteria")
    if asked and cards:
        options = prefer_new(options, [option["id"] for option in cards[-1]["hotels"]])
    trip["hotels"] = options[:MAX_OPTIONS]
    return {"trip": trip, "calls": [*state.get("calls", []), *tool_uses(result.calls)]}
