import copy
from typing import Any, cast

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.utils.trip import (
    PART_ORDER,
    Brief,
    Change,
    Criteria,
    Part,
    Trip,
    apply_updates,
    changed_fields,
    merge_parts,
    missing_fields,
    redo_for,
    today,
)

RECORD_APPROVAL = "record_approval"
ASK_CHANGE = "ask_change"
ASK_MISSING = "ask_missing"
PLAN_CARD = "plan_card"
PART_NODES: dict[Part, str] = {
    "flights": "search_flights",
    "hotels": "search_hotels",
    "itinerary": "draft_itinerary",
}


def _is_new(value: object, stored: str | None) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return value.strip().lower() not in (stored or "").lower()


def _criteria(current: Criteria | None, details: dict[str, Any]) -> tuple[Criteria, list[Change]]:
    merged = copy.deepcopy(current) if current else Criteria()
    changes: list[Change] = []
    flights = details.get("flight_criteria")
    hotels = details.get("hotel_criteria")
    if _is_new(hotels, merged.get("hotels")):
        merged["hotels"] = str(hotels).strip()
        changes.append("hotel")
    if _is_new(flights, merged.get("flights")):
        merged["flights"] = str(flights).strip()
        changes.append("flights")
    return merged, changes


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    if state.get("action") is not None:
        return {}
    trip = copy.deepcopy(state.get("trip") or Trip())
    details = state.get("details") or {}
    before = trip.get("brief") or Brief()
    brief, assumed = apply_updates(before, trip.get("assumed", []), details, today())
    criteria, criteria_changes = _criteria(trip.get("criteria"), details)
    requested = cast("Change | None", state.get("change"))
    changes = [*criteria_changes, *([requested] if requested is not None else [])]
    parts = redo_for(changed_fields(before, brief), None)
    for change in changes:
        parts = merge_parts(parts, redo_for(set(), change))
    if not state.get("cards"):
        parts = list(PART_ORDER)
    trip["brief"] = brief
    trip["assumed"] = assumed
    trip["criteria"] = criteria
    trip["redo"] = merge_parts(trip.get("redo", []), parts)
    return {"trip": trip}


def _next_marked(state: PlannerState, after: Part | None) -> str:
    redo = (state.get("trip") or Trip()).get("redo", [])
    start = 0 if after is None else PART_ORDER.index(after) + 1
    for part in PART_ORDER[start:]:
        if part in redo:
            return PART_NODES[part]
    return PLAN_CARD


def route(state: PlannerState) -> str:
    action = state.get("action")
    if action is not None and action.get("type") == "approve":
        return RECORD_APPROVAL
    if action is not None and action.get("type") == "reject":
        return ASK_CHANGE
    brief = (state.get("trip") or Trip()).get("brief") or Brief()
    if missing_fields(brief):
        return ASK_MISSING
    return _next_marked(state, None)


def after(state: PlannerState, done: Part) -> str:
    return _next_marked(state, done)
