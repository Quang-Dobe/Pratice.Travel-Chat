from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.tracers.langchain import LangChainTracer
from langchain_core.tracers.schemas import Run

from src.utils.usage import UsageTracer, call_metrics, tracing_callbacks

START = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
USAGE = {
    "input_tokens": 1000,
    "output_tokens": 50,
    "total_tokens": 1050,
    "input_token_details": {"cache_read": 400},
}


def make_run(usage: dict[str, Any] | None, tokens: list[tuple[float, str]]) -> Run:
    message: dict[str, Any] = {"kwargs": {"content": "ok", "type": "ai"}}
    if usage is not None:
        message["kwargs"]["usage_metadata"] = usage
    events = [
        {"name": "new_token", "time": START + timedelta(seconds=at), "kwargs": {"token": text}}
        for at, text in tokens
    ]
    return Run(
        name="model",
        run_type="llm",
        start_time=START,
        end_time=START + timedelta(seconds=3),
        inputs={},
        outputs={"generations": [[{"message": message}]]},
        events=[{"name": "start", "time": START}, *events],
    )


def test_a_streamed_call_reports_tokens_cache_ratio_and_first_token_time() -> None:
    run = make_run(USAGE, [(0.2, ""), (0.5, "Hello"), (0.9, " there")])

    assert call_metrics(run) == {
        "input_tokens": 1000,
        "cached_input_tokens": 400,
        "output_tokens": 50,
        "cache_ratio": 0.4,
        "streamed": True,
        "first_token_ms": 500,
    }


def test_a_call_without_streaming_uses_the_whole_latency_as_first_token_time() -> None:
    usage = {"input_tokens": 200, "output_tokens": 10, "total_tokens": 210}

    metrics = call_metrics(make_run(usage, []))

    assert (metrics["cached_input_tokens"], metrics["cache_ratio"]) == (0, 0.0)
    assert (metrics["streamed"], metrics["first_token_ms"]) == (False, 3000)


def test_a_call_without_usage_still_reports_its_timing() -> None:
    metrics = call_metrics(make_run(None, [(1.0, "")]))

    assert metrics == {"streamed": True, "first_token_ms": 1000}


def test_the_tracer_writes_the_metrics_into_the_run_metadata_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[Run] = []
    monkeypatch.setattr(LangChainTracer, "_update_run_single", staticmethod(sent.append))
    tracer = UsageTracer(client=MagicMock())
    run = make_run(USAGE, [(0.5, "Hi")])

    tracer._on_llm_end(run)

    [metadata] = [item.extra["metadata"] for item in sent]
    assert metadata["cache_ratio"] == 0.4
    assert metadata["first_token_ms"] == 500
    assert metadata["usage_metadata"]["input_tokens"] == 1000


def test_no_tracer_is_added_when_tracing_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    assert tracing_callbacks() == []
