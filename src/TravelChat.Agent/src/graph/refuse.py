import re
from typing import Any
from uuid import uuid4

from langgraph.config import get_stream_writer

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.utils.messages import final_message

REFUSAL = (
    "Thanks for asking! I can only help with travel and weather. If you are planning a trip "
    "or want to know the weather somewhere, tell me where you are heading and I will gladly help."
)


async def run(state: PlannerState, deps: PlannerDeps) -> dict[str, Any]:
    writer = get_stream_writer()
    for word in re.findall(r"\S+\s*", REFUSAL):
        writer({"event": "token", "text": word})
    writer({"event": "pills", "items": []})
    return {"messages": [final_message(REFUSAL, str(uuid4()), [])], "pills": []}
