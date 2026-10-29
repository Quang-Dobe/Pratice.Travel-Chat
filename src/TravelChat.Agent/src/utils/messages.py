import json
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.providers.base import ChatProvider
from src.settings import Tier
from src.utils.tools import RawCall, ToolUse
from src.utils.trip import Option

HISTORY_LIMIT = 6
HISTORY_TEXT_LIMIT = 600
CHAT_HISTORY_LIMIT = 12
TURN_TOOLS = "turn_tools"
CARD_ID = "card_id"
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
        options.append(
            Option(id=option_id, summary=summary, priceUsd=round(float(price), 2), deepLink=link)
        )
    return options


def tool_text(calls: Sequence[RawCall], name: str) -> str:
    for call_name, text, ok in reversed(calls):
        if call_name == name and ok:
            return text
    return ""


def final_message(
    text: str, message_id: str | None, uses: Sequence[ToolUse], card_id: str | None = None
) -> AIMessage:
    kwargs: dict[str, Any] = {TURN_TOOLS: [dict(use) for use in uses]}
    if card_id is not None:
        kwargs[CARD_ID] = card_id
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
