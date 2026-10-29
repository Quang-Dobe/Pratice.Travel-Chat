import json
from typing import Any

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.utils.messages import ask, chat_history, final_message
from src.utils.trip import Brief, Trip, today

ASK_CHANGE_PROMPT = """\
You are Q, a travel assistant. The user rejected the plan card that was just shown. In one or two \
short sentences of plain prose, ask what they would like to change about this plan, for example \
the flight, the hotel, the dates, the budget, or the places to visit. Do not use markdown and do \
not list options.
"""


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    brief = (state.get("trip") or Trip()).get("brief") or Brief()
    hint = f"Today is {today().isoformat()}. Current trip details: {json.dumps(brief)}."
    reply = await ask(
        deps.chat(),
        deps.settings.answer_tier,
        ASK_CHANGE_PROMPT,
        hint,
        chat_history(state["messages"]),
    )
    calls = state.get("calls", [])
    return {"messages": [final_message(reply.text, reply.id, calls)]}
