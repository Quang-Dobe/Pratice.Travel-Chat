from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import pytest
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool, ToolException

from src.settings import Settings
from src.utils import tools as tools_module
from src.utils.tools import (
    calculator,
    evaluate_expression,
    load_tools,
    time_tool,
    tool_kind,
    tool_use,
    with_events,
)

type Events = list[dict[str, Any]]


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> Events:
    captured: Events = []
    monkeypatch.setattr(tools_module, "get_stream_writer", lambda: captured.append)
    return captured


def make_settings() -> Settings:
    return Settings(database_url="postgresql://unused", jwks_url="http://unused")


def fake_tool(name: str, reply: str) -> BaseTool:
    async def call(query: str = "") -> str:
        return reply

    return StructuredTool.from_function(coroutine=call, name=name, description=name)


def failing_tool(name: str, error: Exception) -> BaseTool:
    async def call(query: str = "") -> str:
        raise error

    return StructuredTool.from_function(coroutine=call, name=name, description=name)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 2", "3"),
        ("(120 * 3 + 45) / 2", "202.5"),
        ("-4 + 10", "6"),
        ("0.1 + 0.2", "0.3"),
        ("2 * (3 + 4) - 5 / 5", "13"),
    ],
)
def test_calculator_computes(expression: str, expected: str) -> None:
    assert evaluate_expression(expression) == expected


@pytest.mark.parametrize(
    "expression",
    ["x + 1", "abs(1)", "(1).real", "2 ** 3", "'a' * 3", "True + 1", "1 +", "1 / 0", "9" * 400],
)
def test_calculator_rejects_unsafe_or_invalid(expression: str) -> None:
    with pytest.raises(ToolException):
        evaluate_expression(expression)


async def test_calculator_tool_raises_on_unsupported_expression() -> None:
    with pytest.raises(ToolException, match="Unsupported expression"):
        await calculator.ainvoke({"expression": "__import__('os')"})


async def test_time_now_is_utc_iso() -> None:
    result = await time_tool.ainvoke({"op": "now"})

    assert datetime.fromisoformat(result).utcoffset() is not None


async def test_time_days_between() -> None:
    result = await time_tool.ainvoke({"op": "days_between", "a": "2026-01-01", "b": "2026-01-11"})

    assert result == "10"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("2026-01-01", "2026-01-11", "a is 10 days before b"),
        ("2026-01-11", "2026-01-01", "a is 10 days after b"),
        ("2026-01-01", "2026-01-01", "a and b are the same day"),
    ],
)
async def test_time_compare_says_which_date_comes_first(a: str, b: str, expected: str) -> None:
    assert await time_tool.ainvoke({"op": "compare", "a": a, "b": b}) == expected


async def test_time_rejects_missing_or_bad_dates() -> None:
    with pytest.raises(ToolException, match="needs b"):
        await time_tool.ainvoke({"op": "days_between", "a": "2026-01-01"})
    with pytest.raises(ToolException, match="ISO"):
        await time_tool.ainvoke({"op": "compare", "a": "nope", "b": "2026-01-01"})


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("weather__forecast", "retrieve"),
        ("flights__search", "retrieve"),
        ("calculator", "calculate"),
        ("time", "calculate"),
    ],
)
def test_tool_kind(name: str, kind: str) -> None:
    assert tool_kind(name) == kind
    assert tool_use(name) == {"name": name, "kind": kind}


async def test_with_events_writes_started_then_finished(events: Events) -> None:
    wrapped = with_events(calculator)

    result = await wrapped.ainvoke({"expression": "2 + 2"})

    assert result == "4"
    assert events == [
        {"event": "tool_call", "kind": "calculate", "name": "calculator", "status": "started"},
        {"event": "tool_call", "kind": "calculate", "name": "calculator", "status": "finished"},
    ]


def tool_call(args: dict[str, Any]) -> ToolCall:
    return ToolCall(name="x", args=args, id="call-1", type="tool_call")


async def test_with_events_writes_notice_for_no_key_error(events: Events) -> None:
    message = "Hotels data is unavailable: no key set."
    wrapped = with_events(failing_tool("hotels__search", ToolException(message)))

    result = await wrapped.ainvoke(tool_call({"query": "paris"}))

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.content == message
    assert events[0]["status"] == "started"
    assert events[1]["status"] == "finished"
    assert events[1]["kind"] == "retrieve"
    assert events[2] == {"event": "notice", "message": message}
    assert len(events) == 3


async def test_with_events_writes_notice_for_the_free_tier_limit(events: Events) -> None:
    message = "Flights data is unavailable: free-tier limit reached."
    wrapped = with_events(failing_tool("flights__search", ToolException(message)))

    await wrapped.ainvoke(tool_call({"query": "paris"}))

    assert events[-1] == {"event": "notice", "message": message}


async def test_with_events_no_notice_for_success_mentioning_no_key(events: Events) -> None:
    wrapped = with_events(fake_tool("weather__forecast", "the user has no key set"))

    result = await wrapped.ainvoke(tool_call({"query": "paris"}))

    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert [event["event"] for event in events] == ["tool_call", "tool_call"]


async def test_with_events_no_notice_for_other_error(events: Events) -> None:
    wrapped = with_events(failing_tool("hotels__search", ToolException("unavailable right now")))

    result = await wrapped.ainvoke(tool_call({"query": "paris"}))

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert [event["event"] for event in events] == ["tool_call", "tool_call"]


async def test_with_events_finishes_when_inner_raises_unexpected_error(events: Events) -> None:
    wrapped = with_events(failing_tool("weather__forecast", ConnectionError("transport down")))

    with pytest.raises(ConnectionError):
        await wrapped.ainvoke({"query": "paris"})

    assert [event["status"] for event in events] == ["started", "finished"]


async def test_failing_calculator_reaches_model_as_error(events: Events) -> None:
    wrapped = with_events(calculator)

    result = await wrapped.ainvoke(tool_call({"expression": "x + 1"}))

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "Unsupported expression" in str(result.content)


def loader_returning(tools: list[BaseTool]) -> Callable[[Settings], Awaitable[list[BaseTool]]]:
    async def load(_: Settings) -> list[BaseTool]:
        return tools

    return load


async def test_load_tools_groups_mcp_tools_by_domain_in_name_order() -> None:
    remote = [
        fake_tool("weather__forecast", "w"),
        fake_tool("flights__search", "f"),
        fake_tool("weather__alerts", "a"),
    ]

    loaded = await load_tools(make_settings(), loader_returning(remote))

    assert [tool.name for tool in loaded.local] == ["calculator", "time"]
    assert {domain: [tool.name for tool in group] for domain, group in loaded.domains.items()} == {
        "flights": ["flights__search"],
        "hotels": [],
        "weather": ["weather__alerts", "weather__forecast"],
        "places": [],
        "geo": [],
        "search": [],
        "fetch": [],
    }


async def test_load_tools_is_stable_across_calls() -> None:
    remote = [fake_tool("b__x", "b"), fake_tool("a__x", "a")]

    first = await load_tools(make_settings(), loader_returning(remote))
    second = await load_tools(make_settings(), loader_returning(list(reversed(remote))))

    assert {d: [t.name for t in g] for d, g in first.domains.items()} == {
        d: [t.name for t in g] for d, g in second.domains.items()
    }


async def test_load_tools_wraps_every_tool_with_events(events: Events) -> None:
    loaded = await load_tools(make_settings(), loader_returning([fake_tool("hotels__search", "h")]))

    await loaded.for_domain("hotels")[0].ainvoke({"query": "x"})

    assert [(e["kind"], e["name"], e["status"]) for e in events] == [
        ("retrieve", "hotels__search", "started"),
        ("retrieve", "hotels__search", "finished"),
    ]


async def test_load_tools_gives_empty_groups_when_mcp_fails() -> None:
    async def failing(_: Settings) -> list[BaseTool]:
        raise ConnectionError("q-mcp down")

    loaded = await load_tools(make_settings(), failing)

    assert [tool.name for tool in loaded.local] == ["calculator", "time"]
    assert loaded.domains == dict.fromkeys(
        ("flights", "hotels", "weather", "places", "geo", "search", "fetch"), []
    )
    assert loaded.local_named("calculator") is not None
    assert loaded.local_named("timer") is None
    assert loaded.for_domain("flights") == []
