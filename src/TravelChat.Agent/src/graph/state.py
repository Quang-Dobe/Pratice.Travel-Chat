from typing import Any, Literal, NotRequired, TypedDict

from langgraph.graph import MessagesState

from src.utils.tools import ToolUse
from src.utils.trip import Brief, Budget, Day, Option, ReviewPin, Trip

type CardStatus = Literal["open", "approved", "replaced"]


class Picked(TypedDict):
    flightId: str
    hotelId: str


class PlanCard(TypedDict):
    id: str
    messageId: str
    status: CardStatus
    flights: list[Option]
    hotels: list[Option]
    picked: Picked
    days: list[Day]
    budget: Budget
    reviewPins: NotRequired[list[ReviewPin]]


class Pill(TypedDict):
    text: str
    action: str | None


class PlannerState(MessagesState):
    verdict: str
    route: str
    details: dict[str, Any]
    change: str | None
    focus: str | None
    ask_budget: bool
    action: dict[str, Any] | None
    titled: bool
    greeting: str
    trip: Trip
    cards: list[PlanCard]
    pills: list[Pill]
    calls: list[ToolUse]
    note: str | None
    remember: bool
    summarized: Brief | None


def card_payload(card: PlanCard) -> dict[str, Any]:
    return {key: value for key, value in card.items() if key != "messageId"}
