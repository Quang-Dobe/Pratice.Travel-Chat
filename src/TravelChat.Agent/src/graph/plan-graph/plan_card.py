import copy
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from langgraph.config import get_stream_writer

from src.graph.deps import PlannerDeps
from src.graph.state import Picked, PlanCard, PlannerState, card_payload
from src.utils.messages import final_message
from src.utils.trip import (
    Brief,
    Budget,
    Day,
    Option,
    Trip,
    budget,
    daily_cost,
    trip_dates,
    trip_days,
)


def money(value: float) -> str:
    return f"${value:,.2f}"


def budget_text(summary: Budget) -> str:
    hotel = round(summary["totalUsd"] - summary["dailyUsd"], 2)
    breakdown = (
        f"{money(hotel)} hotel + {money(summary['dailyUsd'])} daily costs "
        f"= {money(summary['totalUsd'])} total against a budget of {money(summary['budgetUsd'])}"
    )
    if summary["overBudget"]:
        return (
            f"Warning: this is over budget. {breakdown}. Flights are not counted. "
            "Nothing was changed, so you decide how to proceed."
        )
    return f"Estimated cost: {breakdown}. Flights are listed but not counted."


def keep_pick(options: Sequence[Option], previous: str) -> str:
    ids = [option["id"] for option in options]
    if previous in ids:
        return previous
    return ids[0] if ids else ""


def _options_text(card: PlanCard) -> str:
    lines: list[str] = []
    if card["flights"] and card["hotels"]:
        lines.append("Pick one flight and one hotel below, then approve the trip.")
    if not card["flights"]:
        lines.append("I could not find flight options right now.")
    if not card["hotels"]:
        lines.append("I could not find hotel options right now.")
    return " ".join(lines)


def _new_card(trip: Trip, cards: list[PlanCard], message_id: str) -> PlanCard:
    brief = trip.get("brief") or Brief()
    previous = cards[-1]["picked"] if cards else Picked(flightId="", hotelId="")
    flights = trip.get("flights", [])
    hotels = trip.get("hotels", [])
    picked = Picked(
        flightId=keep_pick(flights, previous["flightId"]),
        hotelId=keep_pick(hotels, previous["hotelId"]),
    )
    hotel = next((option for option in hotels if option["id"] == picked["hotelId"]), None)
    cost = daily_cost(brief)
    days = [
        Day(date=day["date"], activities=day["activities"], weather=day["weather"], costUsd=cost)
        for day in trip.get("days", [])
    ]
    trip["days"] = days
    return PlanCard(
        id=str(uuid4()),
        messageId=message_id,
        status="open",
        flights=flights,
        hotels=hotels,
        picked=picked,
        days=days,
        budget=budget(brief, hotel["priceUsd"] if hotel else 0.0, len(trip_dates(brief))),
    )


def _new_text(trip: Trip, card: PlanCard) -> str:
    brief = trip.get("brief") or Brief()
    return (
        f"Here is your {trip_days(brief)}-day plan for {brief.get('destination', 'your trip')}. "
        f"{_options_text(card)} {budget_text(card['budget'])}"
    )


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    cards: list[PlanCard] = copy.deepcopy(state.get("cards") or [])
    trip = copy.deepcopy(state.get("trip") or Trip())
    note = state.get("note")
    message_id = str(uuid4())
    update: dict[str, Any] = {"note": None}
    card: PlanCard | None
    if note and (not cards or cards[-1]["status"] == "approved"):
        card, text = None, note
    else:
        card = _new_card(trip, cards, message_id)
        for earlier in cards:
            earlier["status"] = "replaced"
        cards.append(card)
        trip["redo"] = []
        text = f"{note} {_new_text(trip, card)}" if note else _new_text(trip, card)
        update.update(cards=cards, trip=trip)
    writer = get_stream_writer()
    writer({"event": "token", "text": text})
    if card is not None:
        writer({"event": "plan_card", **card_payload(card)})
    message = final_message(
        text, message_id, state.get("calls", []), card["id"] if card is not None else None
    )
    update["messages"] = [message]
    return update
