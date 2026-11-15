import copy
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from langgraph.config import get_stream_writer

from src.graph.deps import PlannerDeps
from src.graph.state import Picked, PlanCard, PlannerState, card_payload
from src.utils.geo import GEOCODE_TOOL, geocode, geocode_tool, unique_names
from src.utils.messages import final_message
from src.utils.tools import ToolUse, tool_use
from src.utils.trip import (
    Airport,
    Brief,
    Budget,
    Day,
    Location,
    Option,
    Stop,
    Trip,
    budget,
    daily_cost,
    trip_dates,
    trip_days,
)


def money(value: float) -> str:
    return f"${value:,.2f}"


def budget_text(summary: Budget) -> str:
    breakdown = (
        f"{money(summary['flightUsd'])} flight + {money(summary['hotelUsd'])} hotel + "
        f"{money(summary['dailyUsd'])} daily costs = {money(summary['totalUsd'])} total"
    )
    if summary["budgetUsd"] is None:
        return f"Here is the estimated cost: {breakdown}."
    against = f"{breakdown} against a budget of {money(summary['budgetUsd'])}"
    if summary["overBudget"]:
        return (
            f"Heads up, this is over budget. {against}. "
            "Nothing was changed, so you decide how to proceed."
        )
    return f"Good news, the estimated cost fits your budget: {against}."


def keep_pick(options: Sequence[Option], previous: str) -> str:
    ids = [option["id"] for option in options]
    if previous in ids:
        return previous
    return ids[0] if ids else ""


def _options_text(card: PlanCard) -> str:
    lines: list[str] = []
    if card["flights"] and card["hotels"]:
        lines.append("Pick the flight and the hotel you like below, then approve the trip.")
    if not card["flights"]:
        lines.append("Sorry, I could not find flight options right now.")
    if not card["hotels"]:
        lines.append("Sorry, I could not find hotel options right now.")
    return " ".join(lines)


def _with_map_fields(option: Option) -> Option:
    mapped = copy.deepcopy(option)
    mapped["legs"] = mapped.get("legs") or []
    mapped["location"] = mapped.get("location")
    return mapped


def _unplaced(point: Airport | Stop) -> bool:
    return point["lat"] is None or point["lon"] is None


def airport_query(airport: Airport) -> str:
    return airport["name"] or f"{airport['code']} airport"


def stop_query(stop: Stop, destination: str) -> str:
    return f"{stop['name']}, {destination}" if destination else stop["name"]


def _unplaced_ends(flight: Option) -> list[Airport]:
    ends = [end for leg in flight.get("legs", []) for end in (leg["from"], leg["to"])]
    return [end for end in ends if _unplaced(end)]


def _names_to_place(card: PlanCard, destination: str) -> list[str]:
    picked = [f for f in card["flights"] if f["id"] == card["picked"]["flightId"]]
    others = [f for f in card["flights"] if f["id"] != card["picked"]["flightId"]]
    stops = [stop for day in card["days"] for stop in day.get("stops", [])]
    return unique_names(
        [
            *(airport_query(end) for flight in picked for end in _unplaced_ends(flight)),
            *(stop_query(stop, destination) for stop in stops if _unplaced(stop)),
            *(airport_query(end) for flight in others for end in _unplaced_ends(flight)),
        ]
    )


def _fill_airport(airport: Airport, found: Mapping[str, Location]) -> None:
    location = found.get(airport_query(airport)) if _unplaced(airport) else None
    if location is not None:
        airport["lat"], airport["lon"] = location["lat"], location["lon"]


def _located_stop(stop: Stop, destination: str, found: Mapping[str, Location]) -> Stop | None:
    if not _unplaced(stop):
        return stop
    location = found.get(stop_query(stop, destination))
    if location is None:
        return None
    return Stop(name=stop["name"], lat=location["lat"], lon=location["lon"])


def _place(card: PlanCard, destination: str, found: Mapping[str, Location]) -> None:
    for flight in card["flights"]:
        for leg in flight.get("legs", []):
            _fill_airport(leg["from"], found)
            _fill_airport(leg["to"], found)
    for day in card["days"]:
        located = (_located_stop(stop, destination, found) for stop in day.get("stops", []))
        day["stops"] = [stop for stop in located if stop is not None]


async def _place_on(card: PlanCard, destination: str, deps: PlannerDeps) -> list[ToolUse]:
    names = _names_to_place(card, destination)
    found = await geocode(deps.tools, names)
    _place(card, destination, found)
    return [tool_use(GEOCODE_TOOL)] if names and geocode_tool(deps.tools) is not None else []


def _new_card(trip: Trip, cards: list[PlanCard], message_id: str) -> PlanCard:
    brief = trip.get("brief") or Brief()
    previous = cards[-1]["picked"] if cards else Picked(flightId="", hotelId="")
    flights = [_with_map_fields(option) for option in trip.get("flights", [])]
    hotels = [_with_map_fields(option) for option in trip.get("hotels", [])]
    trip["flights"] = flights
    trip["hotels"] = hotels
    picked = Picked(
        flightId=keep_pick(flights, previous["flightId"]),
        hotelId=keep_pick(hotels, previous["hotelId"]),
    )
    hotel = next((option for option in hotels if option["id"] == picked["hotelId"]), None)
    flight = next((option for option in flights if option["id"] == picked["flightId"]), None)
    cost = daily_cost(brief)
    days = [
        Day(
            date=day["date"],
            activities=day["activities"],
            weather=day["weather"],
            costUsd=cost,
            stops=[Stop(**stop) for stop in day.get("stops", [])],
        )
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
        budget=budget(
            brief,
            hotel["priceUsd"] if hotel else 0.0,
            flight["priceUsd"] if flight else 0.0,
            len(trip_dates(brief)),
        ),
        reviewPins=[],
    )


def _new_text(trip: Trip, card: PlanCard) -> str:
    brief = trip.get("brief") or Brief()
    return (
        f"Here is your {trip_days(brief)}-day plan for {brief.get('destination', 'your trip')}, "
        "and I hope you enjoy it! "
        f"{_options_text(card)} {budget_text(card['budget'])}"
    )


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    cards: list[PlanCard] = copy.deepcopy(state.get("cards") or [])
    trip = copy.deepcopy(state.get("trip") or Trip())
    note = state.get("note")
    message_id = str(uuid4())
    update: dict[str, Any] = {"note": None}
    card: PlanCard | None
    calls = list(state.get("calls", []))
    if note and (not cards or cards[-1]["status"] == "approved"):
        card, text = None, note
    else:
        card = _new_card(trip, cards, message_id)
        destination = (trip.get("brief") or Brief()).get("destination", "")
        calls.extend(await _place_on(card, destination, deps))
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
    message = final_message(text, message_id, calls, card["id"] if card is not None else None)
    update["messages"] = [message]
    return update
