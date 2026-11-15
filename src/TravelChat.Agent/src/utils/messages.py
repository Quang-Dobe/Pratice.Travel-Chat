import json
import math
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.providers.base import ChatProvider
from src.settings import Tier
from src.utils.tools import RawCall, ToolUse
from src.utils.trip import Airport, Leg, Location, Option

MAX_LATITUDE = 90.0
MAX_LONGITUDE = 180.0
MAX_LEGS = 8
HISTORY_LIMIT = 6
HISTORY_TEXT_LIMIT = 600
CHAT_HISTORY_LIMIT = 12
TURN_TOOLS = "turn_tools"
CARD_ID = "card_id"
SOURCES = "sources"
ARTIFACTS = "artifacts"
ACTION = "action"


def transcript(messages: Sequence[BaseMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        text = message.text.strip()
        if not text:
            continue
        if isinstance(message, HumanMessage):
            lines.append(f"User: {text[:HISTORY_TEXT_LIMIT]}")
        elif isinstance(message, AIMessage):
            lines.append(f"Assistant: {text[:HISTORY_TEXT_LIMIT]}")
    return "\n".join(lines[-HISTORY_LIMIT:])


def first_user_text(messages: Sequence[BaseMessage]) -> str:
    for message in messages:
        if isinstance(message, HumanMessage):
            return message.text
    return ""


def last_user_message(messages: Sequence[BaseMessage]) -> HumanMessage | None:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    return None


def chat_history(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    recent: list[BaseMessage] = [
        message
        for message in messages
        if isinstance(message, HumanMessage | AIMessage) and message.text.strip()
    ]
    kept = recent[-CHAT_HISTORY_LIMIT:]
    while kept and not isinstance(kept[0], HumanMessage):
        kept.pop(0)
    return kept


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def parse_options(text: str) -> list[Option]:
    data = parse_json(text)
    if isinstance(data, dict):
        data = data.get("options") or data.get("items") or data.get("data")
    if not isinstance(data, list):
        return []
    options: list[Option] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        option_id = item.get("id")
        summary = item.get("summary")
        price = item.get("priceUsd")
        link = item.get("deepLink")
        if not isinstance(option_id, str) or not option_id or option_id in seen:
            continue
        if not isinstance(summary, str) or isinstance(price, bool):
            continue
        if not isinstance(price, int | float):
            continue
        if not isinstance(link, str) or not link.startswith("https://"):
            continue
        seen.add(option_id)
        option = Option(
            id=option_id, summary=summary, priceUsd=round(float(price), 2), deepLink=link
        )
        if legs := parse_legs(item.get("legs")):
            option["legs"] = legs
        if (location := parse_location(item.get("location"))) is not None:
            option["location"] = location
        options.append(option)
    return options


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _coordinate(value: object, limit: float) -> float | None:
    number = _number(value)
    return number if number is not None and -limit <= number <= limit else None


def latitude(value: object) -> float | None:
    return _coordinate(value, MAX_LATITUDE)


def longitude(value: object) -> float | None:
    return _coordinate(value, MAX_LONGITUDE)


def parse_location(value: object) -> Location | None:
    if not isinstance(value, dict):
        return None
    lat = latitude(value.get("lat"))
    lon = longitude(value.get("lon"))
    if lat is None or lon is None:
        return None
    return Location(lat=lat, lon=lon)


def _airport(value: object) -> Airport | None:
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    name = value.get("name")
    if not isinstance(code, str) or not code.strip() or not isinstance(name, str):
        return None
    return Airport(
        code=code.strip(),
        name=name.strip(),
        lat=latitude(value.get("lat")),
        lon=longitude(value.get("lon")),
    )


def _leg(value: object) -> Leg | None:
    if not isinstance(value, dict):
        return None
    start = _airport(value.get("from"))
    end = _airport(value.get("to"))
    departs = value.get("departs")
    arrives = value.get("arrives")
    if start is None or end is None:
        return None
    if not isinstance(departs, str) or not departs.strip():
        return None
    if not isinstance(arrives, str) or not arrives.strip():
        return None
    return Leg(**{"from": start, "to": end, "departs": departs.strip(), "arrives": arrives.strip()})


def parse_legs(value: object) -> list[Leg]:
    if not isinstance(value, list) or len(value) > MAX_LEGS:
        return []
    legs: list[Leg] = []
    for item in value:
        leg = _leg(item)
        if leg is None:
            return []
        legs.append(leg)
    return legs


def tool_text(calls: Sequence[RawCall], name: str) -> str:
    for call_name, text, ok in reversed(calls):
        if call_name == name and ok:
            return text
    return ""


def final_message(
    text: str,
    message_id: str | None,
    uses: Sequence[ToolUse],
    card_id: str | None = None,
    sources: Sequence[Mapping[str, Any]] | None = None,
    artifacts: Sequence[Mapping[str, Any]] | None = None,
) -> AIMessage:
    kwargs: dict[str, Any] = {TURN_TOOLS: [dict(use) for use in uses]}
    if card_id is not None:
        kwargs[CARD_ID] = card_id
    if sources:
        kwargs[SOURCES] = [dict(item) for item in sources]
    if artifacts:
        kwargs[ARTIFACTS] = [dict(item) for item in artifacts]
    return AIMessage(text, id=message_id or str(uuid4()), additional_kwargs=kwargs)


async def ask(
    provider: ChatProvider,
    tier: Tier,
    prompt: str,
    hint: str,
    messages: Sequence[BaseMessage],
) -> AIMessage:
    model = provider.model(tier)
    return await model.ainvoke([provider.system(prompt, hint), *messages])
