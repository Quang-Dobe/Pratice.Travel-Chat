from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI
from pydantic import SecretStr

from src.providers.openai_chat import MAX_TOKENS, OpenAIChatProvider


class AzureOpenAIProvider(OpenAIChatProvider):
    def _build(self, model_id: str) -> BaseChatModel:
        kwargs: dict[str, Any] = {
            "model": model_id,
            "azure_deployment": model_id,
            "azure_endpoint": self._settings.azure_openai_endpoint.strip(),
            "api_version": self._settings.azure_openai_api_version.strip(),
            "api_key": SecretStr(self._settings.azure_key()),
            "max_tokens": MAX_TOKENS,
            "stream_usage": True,
        }
        return AzureChatOpenAI(**kwargs)
