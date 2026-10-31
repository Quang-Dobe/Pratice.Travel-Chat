import copy
from typing import Any

from src.graph.deps import PlannerDeps
from src.graph.state import Picked, PlanCard, PlannerState
from src.utils.trip import Brief, Trip, budget, trip_dates

NO_CARD = "There is no plan to approve yet. Tell me about your trip and I will put one together."
OLD_CARD = "That Approve belongs to an older plan, so nothing was approved."
ALREADY_APPROVED = "This trip is already approved."
WRONG_PICK = "The picked flight or hotel is not on the current plan, so nothing was approved."


def _refusal(card: PlanCard | None, action: dict[str, Any]) -> str | None:
    if card is None:
        return NO_CARD
    if action.get("cardId") != card["id"] or card["status"] == "replaced":
        return OLD_CARD
    if card["status"] == "approved":
        return ALREADY_APPROVED
    flights = {option["id"] for option in card["flights"]}
    hotels = {option["id"] for option in card["hotels"]}
    if action.get("flightId") not in flights or action.get("hotelId") not in hotels:
        return WRONG_PICK
    return None


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    cards: list[PlanCard] = copy.deepcopy(state.get("cards") or [])
    action = state.get("action") or {}
    latest = cards[-1] if cards else None
    refusal = _refusal(latest, action)
    if refusal is not None or latest is None:
        return {"note": refusal}
    latest["picked"] = Picked(flightId=action["flightId"], hotelId=action["hotelId"])
    hotel = next(option for option in latest["hotels"] if option["id"] == action["hotelId"])
    flight = next(option for option in latest["flights"] if option["id"] == action["flightId"])
    brief = (state.get("trip") or Trip()).get("brief") or Brief()
    days = len(trip_dates(brief))
    latest["budget"] = budget(brief, hotel["priceUsd"], flight["priceUsd"], days)
    latest["status"] = "approved"
    return {"cards": cards, "note": None}
