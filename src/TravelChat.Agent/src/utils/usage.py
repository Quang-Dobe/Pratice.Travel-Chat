from datetime import datetime
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.langchain import (
    LangChainTracer,
    _get_usage_metadata_from_generations,
)
from langchain_core.tracers.schemas import Run
from langsmith.utils import tracing_is_enabled

NEW_TOKEN = "new_token"


def _first_token_time(run: Run) -> datetime | None:
    tokens = [event for event in run.events or [] if event.get("name") == NEW_TOKEN]
    with_text = [event for event in tokens if event.get("kwargs", {}).get("token")]
    candidates = with_text or tokens
    time = candidates[0].get("time") if candidates else None
    return time if isinstance(time, datetime) else None


def call_metrics(run: Run) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    generations = (run.outputs or {}).get("generations")
    usage = _get_usage_metadata_from_generations(generations) if generations else None
    if usage is not None:
        input_tokens = usage.get("input_tokens", 0)
        cached = (usage.get("input_token_details") or {}).get("cache_read") or 0
        metrics["input_tokens"] = input_tokens
        metrics["cached_input_tokens"] = cached
        metrics["output_tokens"] = usage.get("output_tokens", 0)
        metrics["cache_ratio"] = round(cached / input_tokens, 4) if input_tokens else 0.0
    first = _first_token_time(run)
    metrics["streamed"] = first is not None
    until = first or run.end_time
    if until is not None:
        metrics["first_token_ms"] = round((until - run.start_time).total_seconds() * 1000)
    return metrics


class UsageTracer(LangChainTracer):
    def _on_llm_end(self, run: Run) -> None:
        run.extra.setdefault("metadata", {}).update(call_metrics(run))
        super()._on_llm_end(run)


def tracing_callbacks() -> list[BaseCallbackHandler]:
    return [UsageTracer()] if tracing_is_enabled() else []
