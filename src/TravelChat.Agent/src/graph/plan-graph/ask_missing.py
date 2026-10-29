import json
from typing import Any

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.utils.messages import ask, chat_history, final_message
from src.utils.trip import Brief, Trip, missing_fields, today

ASK_MISSING_PROMPT = """\
You are Q, a travel assistant planning a trip with the user. The trip details are incomplete. In \
one or two short sentences of plain prose, ask the user for the missing details named below. Do \
not guess them, do not search, do not use markdown, and do not list options.
"""


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    brief = (state.get("trip") or Trip()).get("brief") or Brief()
    hint = (
        f"Today is {today().isoformat()}. Trip details so far: {json.dumps(brief)}. "
        f"Missing: {', '.join(missing_fields(brief))}."
    )
    reply = await ask(
        deps.chat(),
        deps.settings.answer_tier,
        ASK_MISSING_PROMPT,
        hint,
        chat_history(state["messages"]),
    )
    calls = state.get("calls", [])
    return {"messages": [final_message(reply.text, reply.id, calls)]}
