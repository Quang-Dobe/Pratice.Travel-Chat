import asyncio
import re
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

PLACEHOLDER = "This is a placeholder reply — the travel planner is not connected yet."
WORD_DELAY_SECONDS = 0.03
NODE_NAME = "placeholder_reply"
RUN_NAME = "chat-turn"

type ChatGraph = CompiledStateGraph[MessagesState, None, MessagesState, MessagesState]


async def placeholder_reply(state: MessagesState) -> dict[str, list[BaseMessage]]:
    writer = get_stream_writer()
    for word in re.findall(r"\S+\s*", PLACEHOLDER):
        writer({"text": word})
        await asyncio.sleep(WORD_DELAY_SECONDS)
    return {"messages": [AIMessage(PLACEHOLDER)]}


def build_graph(checkpointer: BaseCheckpointSaver[str]) -> ChatGraph:
    builder = StateGraph(MessagesState)
    builder.add_node(NODE_NAME, placeholder_reply)
    builder.add_edge(START, NODE_NAME)
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
