from typing import Any
from uuid import uuid4

from langgraph.config import get_stream_writer

from src.graph.deps import PlannerDeps
from src.graph.state import PlanCard, PlannerState
from src.utils.messages import final_message
from src.utils.trip import Option


def _chosen(options: list[Option], option_id: str) -> Option:
    return next(option for option in options if option["id"] == option_id)


def confirmation(card: PlanCard) -> str:
    flight = _chosen(card["flights"], card["picked"]["flightId"])
    hotel = _chosen(card["hotels"], card["picked"]["hotelId"])
    return (
        "Wonderful, your trip is confirmed! Have a great journey.\n"
        f"Flight: {flight['summary']} (${flight['priceUsd']:,.2f}). Book it here: "
        f"{flight['deepLink']}\n"
        f"Hotel: {hotel['summary']} (${hotel['priceUsd']:,.2f}). Book it here: "
        f"{hotel['deepLink']}\n"
        "Prices and availability are informational and may have changed."
    )


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    card = state["cards"][-1]
    text = confirmation(card)
    writer = get_stream_writer()
    writer({"event": "token", "text": text})
    message = final_message(text, str(uuid4()), state.get("calls", []), card["id"])
    return {"messages": [message]}
