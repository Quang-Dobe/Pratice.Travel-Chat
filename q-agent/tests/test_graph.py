from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from q_agent.conversations import graph as graph_module
from q_agent.conversations.graph import PLACEHOLDER, ChatGraph, build_graph, thread_config


@pytest.fixture(autouse=True)
def no_word_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "WORD_DELAY_SECONDS", 0)


@pytest.fixture
def chat_graph() -> ChatGraph:
    return build_graph(InMemorySaver())


async def run_turn(chat_graph: ChatGraph, config: Any, text: str) -> list[str]:
    stream: AsyncIterator[Any] = chat_graph.astream(
        {"messages": [HumanMessage(text)]}, config, stream_mode="custom", version="v2"
    )
    return [part["data"]["text"] async for part in stream]


async def test_chunks_join_to_the_placeholder(chat_graph: ChatGraph) -> None:
    config = thread_config(uuid4(), uuid4())

    chunks = await run_turn(chat_graph, config, "hello")

    assert len(chunks) > 1
    assert "".join(chunks) == PLACEHOLDER


async def test_thread_holds_user_and_assistant_after_one_turn(chat_graph: ChatGraph) -> None:
    config = thread_config(uuid4(), uuid4())

    await run_turn(chat_graph, config, "hello")

    messages = (await chat_graph.aget_state(config)).values["messages"]
    assert [type(message) for message in messages] == [HumanMessage, AIMessage]
    assert messages[0].text == "hello"
    assert messages[1].text == PLACEHOLDER


async def test_thread_holds_four_messages_after_a_second_turn(chat_graph: ChatGraph) -> None:
    config = thread_config(uuid4(), uuid4())

    await run_turn(chat_graph, config, "first")
    await run_turn(chat_graph, config, "second")

    messages = (await chat_graph.aget_state(config)).values["messages"]
    assert [message.text for message in messages] == ["first", PLACEHOLDER, "second", PLACEHOLDER]


async def test_threads_of_different_users_are_isolated(chat_graph: ChatGraph) -> None:
    conversation_id = uuid4()
    mine = thread_config(uuid4(), conversation_id)
    theirs = thread_config(uuid4(), conversation_id)

    await run_turn(chat_graph, mine, "hello")

    assert (await chat_graph.aget_state(theirs)).values == {}
