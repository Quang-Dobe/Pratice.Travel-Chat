from collections.abc import Sequence
from typing import Any, cast

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr

from src.settings import TIERS, Settings, Tier

MAX_TOKENS = 4096


class OpenAIChatProvider:
    errors: type[Exception] = openai.APIError

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._models: dict[Tier, BaseChatModel] = {
            tier: self._build(settings.model_for(tier)) for tier in TIERS
        }

    def _build(self, model_id: str) -> BaseChatModel:
        key = self._settings.openai_key()
        kwargs: dict[str, Any] = {
            "model": model_id,
            "api_key": SecretStr(key),
            "max_tokens": MAX_TOKENS,
            "default_headers": {"api-key": key},
            "stream_usage": True,
        }
        base_url = self._settings.openai_base_url.strip()
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)

    def model(self, tier: Tier) -> BaseChatModel:
        return self._models[tier]

    def system(self, prompt: str, hint: str) -> SystemMessage:
        return SystemMessage(content=f"{prompt}\n\n{hint}" if hint else prompt)

    def with_tools(
        self, model: BaseChatModel, tools: Sequence[BaseTool], final: bool
    ) -> Runnable[Any, Any]:
        openai_model = cast("ChatOpenAI", model)
        if final:
            return openai_model.bind_tools(tools, tool_choice="none")
        return openai_model.bind_tools(tools)

    def structured(self, model: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, Any]:
        return model.with_structured_output(schema, method="json_schema", strict=True)
