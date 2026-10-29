import json
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.providers.base import structured_call
from src.utils.messages import transcript
from src.utils.trip import Brief, Criteria, Trip, diff_brief, today, trip_days

EXTRACT_PROMPT = """\
Update the trip brief of this travel conversation after the latest user message.
- brief: return the WHOLE brief as it stands after the latest message. Copy every value the latest \
message does not change exactly as it appears in the current brief; use null only for a value that \
is still unknown. Dates are ISO yyyy-MM-dd. length_days is the number of days from start_date to \
end_date, both included. budget_level is low, mid or high; budget_usd only for an amount the user \
stated in USD. interests are short words such as food or hiking.
- flight_criteria and hotel_criteria: what the user wants from the flight and from the hotel, as \
short phrases such as "cheaper" or "nonstop". Copy the current ones when the latest message does \
not change them.
- change: flights or hotel only when the latest message asks for a different flight or hotel than \
the one on the plan card; otherwise null.
"""

CRITERIA_FIELDS = {"flight_criteria": "flights", "hotel_criteria": "hotels"}


class Details(BaseModel):
    destination: str | None
    origin: str | None
    start_date: str | None
    end_date: str | None
    length_days: int | None
    travellers: int | None
    budget_level: Literal["low", "mid", "high"] | None
    budget_usd: float | None
    interests: list[str] | None
    flight_criteria: str | None
    hotel_criteria: str | None


class Extraction(BaseModel):
    brief: Details
    change: Literal["flights", "hotel"] | None


def _current(brief: Brief, criteria: Criteria) -> dict[str, Any]:
    current: dict[str, Any] = dict(brief)
    current["length_days"] = trip_days(brief) or None
    for field, key in CRITERIA_FIELDS.items():
        current[field] = criteria.get(key)
    return current


def _prompt(state: PlannerState, brief: Brief, criteria: Criteria) -> str:
    messages = state["messages"]
    cards = state.get("cards") or []
    earlier: list[BaseMessage] = list(messages[:-1])
    latest = messages[-1].text if messages else ""
    card = f"Plan card on screen: {cards[-1]['status']}." if cards else "No plan card is shown yet."
    return "\n\n".join(
        [
            f"Today is {today().isoformat()}. {card}",
            f"Current brief: {json.dumps(_current(brief, criteria))}",
            f"Earlier conversation:\n{transcript(earlier) or '(none)'}",
            f"Latest user message: {latest}",
        ]
    )


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    if state.get("action") is not None:
        return {}
    trip = state.get("trip") or Trip()
    brief = trip.get("brief") or Brief()
    criteria = trip.get("criteria") or Criteria()
    provider = deps.chat()
    result = await structured_call(
        provider,
        provider.model(deps.settings.answer_tier),
        Extraction,
        [SystemMessage(EXTRACT_PROMPT), HumanMessage(_prompt(state, brief, criteria))],
    )
    updated = result.brief.model_dump(exclude_none=True)
    details = diff_brief(brief, updated)
    for field in CRITERIA_FIELDS:
        if field in updated:
            details[field] = updated[field]
    return {"details": details, "change": result.change}
