import asyncio
import json
import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.providers.base import ChatProvider, quiet, structured_call
from src.settings import Tier
from src.utils.messages import ACTION, last_user_message, transcript
from src.utils.trip import Trip, today

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 60

CLASSIFY_PROMPT = """\
Classify the latest user message of this travel and weather conversation.
- verdict: travel for trips, destinations, attractions, flights, hotels, budgets, or travel dates, \
including follow-ups to an earlier travel discussion; weather for a plain question about the \
weather or climate somewhere with no trip being planned; off_topic for anything else.
- route: plan when the user is planning a trip or asks for a plan, itinerary, or recommendation, \
answers a question the planner asked about the trip, or changes any detail or choice of the trip \
in progress. answer for a plain weather question or a simple factual question.
"""

TITLE_PROMPT = (
    "Write a title of at most six words for a conversation that starts with the user message "
    "below. Reply with the title only, without quotes or punctuation at the end."
)


class Classification(BaseModel):
    verdict: Literal["travel", "weather", "off_topic"]
    route: Literal["answer", "plan"]


async def write_title(provider: ChatProvider, tier: Tier, text: str) -> str | None:
    try:
        reply = await quiet(provider.model(tier)).ainvoke(
            [SystemMessage(TITLE_PROMPT), HumanMessage(text)]
        )
    except Exception:
        logger.exception("Title generation failed; keeping the existing title")
        return None
    title = " ".join(str(reply.text).split()).strip("\"'“”‘’ .")
    return title[:MAX_TITLE_LENGTH].rstrip() or None


def _context(state: PlannerState) -> str:
    trip = state.get("trip") or Trip()
    cards = state.get("cards") or []
    lines = [f"Today is {today().isoformat()}."]
    if trip.get("brief"):
        lines.append(f"Current trip brief: {json.dumps(trip['brief'])}")
    lines.append(
        f"Plan card on screen: {cards[-1]['status']}." if cards else "No plan card is shown yet."
    )
    return "\n".join(lines)


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    messages = state["messages"]
    last = last_user_message(messages)
    action = last.additional_kwargs.get(ACTION) if last is not None else None
    reset: dict[str, Any] = {
        "action": action,
        "calls": [],
        "note": None,
        "pills": [],
        "details": {},
        "change": None,
    }
    if action is not None:
        return {**reset, "verdict": "travel", "route": "plan"}
    provider = deps.chat()
    deciding = structured_call(
        provider,
        provider.model(deps.settings.answer_tier),
        Classification,
        [
            SystemMessage(CLASSIFY_PROMPT),
            HumanMessage(f"{_context(state)}\n\n{transcript(messages)}"),
        ],
    )
    title: str | None = None
    if state.get("titled", False):
        decision = await deciding
    else:
        decision, title = await asyncio.gather(
            deciding, write_title(provider, deps.settings.title_tier, last.text if last else "")
        )
    update: dict[str, Any] = {
        **reset,
        "verdict": decision.verdict,
        "route": decision.route,
    }
    if title is not None and decision.verdict != "off_topic":
        get_stream_writer()({"event": "title", "title": title})
        update["titled"] = True
    return update
