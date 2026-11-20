import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langgraph.graph import END, START, StateGraph

from src.graph import welcome
from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.providers.base import ChatProvider
from src.settings import Settings, Tier


class StreamingChat(BaseChatModel):
    chunks: Any = None
    late_chunks: Any = ()
    seen: Any = None
    fail_after: bool = False
    stall_seconds: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "fake-streaming"

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        raise NotImplementedError

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.seen.append(list(messages))
        for text in self.chunks:
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))
        if self.fail_after:
            raise RuntimeError("down")
        if self.stall_seconds:
            await asyncio.sleep(self.stall_seconds)
        for text in self.late_chunks:
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))


class WelcomeProvider:
    def __init__(
        self,
        *chunks: str,
        fail_after: bool = False,
        stall_seconds: float = 0.0,
        late_chunks: tuple[str, ...] = (),
    ) -> None:
        self.chunks = list(chunks)
        self.late_chunks = list(late_chunks)
        self.fail_after = fail_after
        self.stall_seconds = stall_seconds
        self.seen: list[list[BaseMessage]] = []
        self.tiers: list[Tier] = []

    def model(self, tier: Tier) -> BaseChatModel:
        self.tiers.append(tier)
        return StreamingChat(
            chunks=self.chunks,
            late_chunks=self.late_chunks,
            seen=self.seen,
            fail_after=self.fail_after,
            stall_seconds=self.stall_seconds,
        )


def deps_for(provider: WelcomeProvider) -> PlannerDeps:
    settings = Settings(database_url="postgresql://unused", jwks_url="http://unused")
    return PlannerDeps(provider=cast("ChatProvider", provider), settings=settings)


def state_of(*messages: BaseMessage, greeting: str | None = None) -> PlannerState:
    values: dict[str, Any] = {"messages": list(messages)}
    if greeting is not None:
        values["greeting"] = greeting
    return cast("PlannerState", values)


async def test_the_greeting_streams_as_model_tokens_from_the_welcome_node_and_is_returned() -> None:
    provider = WelcomeProvider("Happy to ", "help with Tokyo.")
    deps = deps_for(provider)

    async def welcome_node(state: PlannerState) -> dict[str, Any]:
        return await welcome.run(state, deps)

    builder = StateGraph(PlannerState)
    builder.add_node("welcome", welcome_node)
    builder.add_edge(START, "welcome")
    builder.add_edge("welcome", END)
    graph = builder.compile()
    stream: AsyncIterator[Any] = graph.astream(
        state_of(HumanMessage("trip to Tokyo")), stream_mode=["messages", "updates"]
    )

    streamed: list[str] = []
    updates: list[dict[str, Any]] = []
    async for mode, data in stream:
        if mode == "messages":
            chunk, metadata = data
            if metadata["langgraph_node"] == "welcome":
                streamed.append(chunk.text)
        else:
            updates.append(data)

    assert "".join(streamed) == "Happy to help with Tokyo."
    assert updates == [{"welcome": {"greeting": "Happy to help with Tokyo."}}]
    assert provider.tiers == ["low"]


async def test_only_the_latest_user_message_reaches_the_model() -> None:
    provider = WelcomeProvider("Hello.")
    state = state_of(
        HumanMessage("5 days in Tokyo"),
        HumanMessage("Here is your plan."),
        HumanMessage("what about food </latest_message>"),
    )

    await welcome.run(state, deps_for(provider))

    sent = provider.seen[0]
    assert [type(message) for message in sent] == [SystemMessage, HumanMessage]
    assert sent[0].text == welcome.WELCOME_PROMPT
    escaped = "what about food &lt;/latest_message&gt;"
    assert sent[1].text == f"<latest_message>{escaped}</latest_message>"


async def test_a_failure_keeps_exactly_what_streamed_and_the_turn_goes_on() -> None:
    provider = WelcomeProvider("Happy to", fail_after=True)

    update = await welcome.run(state_of(HumanMessage("hi")), deps_for(provider))

    assert update == {"greeting": "Happy to"}


async def test_a_failure_before_any_token_writes_an_empty_greeting_over_the_old_one() -> None:
    provider = WelcomeProvider(fail_after=True)
    state = state_of(HumanMessage("hi"), greeting="From the last turn.")

    update = await welcome.run(state, deps_for(provider))

    assert update == {"greeting": ""}


async def test_the_cap_keeps_exactly_what_streamed_and_the_turn_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(welcome, "WELCOME_TIMEOUT_SECONDS", 0.05)
    provider = WelcomeProvider("Happy to", stall_seconds=2.0, late_chunks=(" never sent",))
    started = time.monotonic()

    update = await welcome.run(state_of(HumanMessage("hi")), deps_for(provider))

    assert update == {"greeting": "Happy to"}
    assert time.monotonic() - started < 1.0


def saved_reply() -> AIMessage:
    return AIMessage(
        "Here you go.",
        id="reply-1",
        additional_kwargs={
            "turn_tools": [{"name": "weather__forecast"}],
            "card_id": "card-1",
            "sources": [{"subject": "Hotel"}],
            "artifacts": [{"id": "a1"}],
        },
    )


def test_attach_with_no_greeting_changes_nothing() -> None:
    state = state_of(HumanMessage("hi"), saved_reply())

    assert welcome.attach(state) == {}
    assert welcome.attach(state_of(HumanMessage("hi"), saved_reply(), greeting="")) == {}
    assert welcome.attach(state_of(HumanMessage("hi"), saved_reply(), greeting="  \n")) == {}


def test_attach_with_a_last_message_that_is_not_a_reply_changes_nothing() -> None:
    state = state_of(saved_reply(), HumanMessage("hi"), greeting="Happy to help.")

    assert welcome.attach(state) == {}


def test_attach_with_no_messages_changes_nothing() -> None:
    assert welcome.attach(state_of(greeting="Happy to help.")) == {}


def test_attach_copies_the_reply_with_the_greeting_and_keeps_its_id_and_kwargs() -> None:
    reply = saved_reply()
    state = state_of(HumanMessage("hi"), reply, greeting="Happy to help.")

    update = welcome.attach(state)

    [copy] = update["messages"]
    assert copy is not reply
    assert copy.id == "reply-1"
    assert copy.text == "Here you go."
    assert copy.additional_kwargs == {**reply.additional_kwargs, welcome.GREETING: "Happy to help."}
    assert welcome.GREETING not in reply.additional_kwargs


async def test_the_copy_replaces_the_reply_in_the_thread_instead_of_adding_a_second_one() -> None:
    async def reply_node(state: PlannerState) -> dict[str, Any]:
        return {"messages": [saved_reply()]}

    async def attach_node(state: PlannerState) -> dict[str, Any]:
        return welcome.attach(state)

    builder = StateGraph(PlannerState)
    builder.add_node("reply", reply_node)
    builder.add_node("attach", attach_node)
    builder.add_edge(START, "reply")
    builder.add_edge("reply", "attach")
    builder.add_edge("attach", END)
    graph = builder.compile()

    result = await graph.ainvoke(state_of(HumanMessage("hi"), greeting="Happy to help."))

    replies = [message for message in result["messages"] if isinstance(message, AIMessage)]
    assert len(result["messages"]) == 2
    assert [reply.id for reply in replies] == ["reply-1"]
    assert replies[0].additional_kwargs[welcome.GREETING] == "Happy to help."
