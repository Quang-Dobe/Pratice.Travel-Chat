import asyncio
from typing import Any, Literal
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.graph import answer, classify, refuse, suggest, summarize, welcome
from src.graph.deps import PlannerDeps
from src.graph.plan import build_plan
from src.graph.state import PlannerState

RUN_NAME = "chat-turn"

CLASSIFY = "classify"
REFUSE = "refuse"
WELCOME = "welcome"
ANSWER = "answer"
PLAN = "plan"
SUGGEST = "suggest"

type ChatGraph = CompiledStateGraph[PlannerState, None, PlannerState, PlannerState]


def after_classify(state: PlannerState) -> Literal["refuse", "answer", "plan"]:
    if state.get("verdict") == "off_topic":
        return "refuse"
    if state.get("route") == "plan":
        return "plan"
    return "answer"


def build_graph(checkpointer: BaseCheckpointSaver[str], deps: PlannerDeps) -> ChatGraph:
    async def classify_node(state: PlannerState) -> dict[str, Any]:
        return await classify.run(state, deps)

    async def refuse_node(state: PlannerState) -> dict[str, Any]:
        return await refuse.run(state, deps)

    async def welcome_node(state: PlannerState) -> dict[str, Any]:
        return await welcome.run(state, deps)

    async def answer_node(state: PlannerState) -> dict[str, Any]:
        return await answer.run(state, deps)

    async def suggest_node(state: PlannerState) -> dict[str, Any]:
        suggested, summarized = await asyncio.gather(
            suggest.run(state, deps), summarize.run(state, deps)
        )
        return {**suggested, **summarized, **welcome.attach(state)}

    builder = StateGraph(PlannerState)
    builder.add_node(CLASSIFY, classify_node)
    builder.add_node(REFUSE, refuse_node)
    builder.add_node(WELCOME, welcome_node)
    builder.add_node(ANSWER, answer_node)
    builder.add_node(PLAN, build_plan(deps))
    builder.add_node(SUGGEST, suggest_node)
    builder.add_edge(START, CLASSIFY)
    builder.add_conditional_edges(
        CLASSIFY, after_classify, {REFUSE: REFUSE, ANSWER: WELCOME, PLAN: WELCOME}
    )
    builder.add_conditional_edges(WELCOME, after_classify, [ANSWER, PLAN])
    builder.add_edge(REFUSE, END)
    builder.add_edge(ANSWER, SUGGEST)
    builder.add_edge(PLAN, SUGGEST)
    builder.add_edge(SUGGEST, END)
    return builder.compile(checkpointer=checkpointer)


def thread_id_for(uid: UUID, conversation_id: UUID) -> str:
    return f"{uid}:{conversation_id}"


def thread_config(uid: UUID, conversation_id: UUID) -> RunnableConfig:
    thread_id = thread_id_for(uid, conversation_id)
    return {
        "configurable": {"thread_id": thread_id},
        "metadata": {
            "thread_id": thread_id,
            "uid": str(uid),
            "conversation_id": str(conversation_id),
        },
        "run_name": RUN_NAME,
    }
