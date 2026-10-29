from collections.abc import Sequence
from typing import Any, cast

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel, SecretStr

from src.settings import TIERS, Settings, Tier

MAX_TOKENS = 4096
CACHE_CONTROL = {"type": "ephemeral"}


class AnthropicProvider:
    errors: type[Exception] = anthropic.APIError

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._models: dict[Tier, BaseChatModel] = {
            tier: self._build(settings.model_for(tier)) for tier in TIERS
        }

    def _build(self, model_id: str) -> BaseChatModel:
        kwargs: dict[str, Any] = {
            "model_name": model_id,
            "max_tokens_to_sample": MAX_TOKENS,
            "timeout": None,
            "stop": None,
            "api_key": SecretStr(self._settings.anthropic_key()),
        }
        base_url = self._settings.anthropic_base_url.strip()
        if base_url:
            kwargs["base_url"] = base_url
        return ChatAnthropic(**kwargs)

    def model(self, tier: Tier) -> BaseChatModel:
        return self._models[tier]

    def system(self, prompt: str, hint: str) -> SystemMessage:
        blocks: list[str | dict[Any, Any]] = [
            {"type": "text", "text": prompt, "cache_control": dict(CACHE_CONTROL)}
        ]
        if hint:
            blocks.append({"type": "text", "text": hint})
        return SystemMessage(content=blocks)

    def with_tools(
        self, model: BaseChatModel, tools: Sequence[BaseTool], final: bool
    ) -> Runnable[Any, Any]:
        anthropic_model = cast("ChatAnthropic", model)
        if final:
            return anthropic_model.bind_tools(tools, tool_choice={"type": "none"})
        return anthropic_model.bind_tools(tools)

    def structured(self, model: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, Any]:
        return model.with_structured_output(schema, method="json_schema")
