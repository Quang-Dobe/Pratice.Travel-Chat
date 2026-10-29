from collections.abc import Sequence
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.constants import TAG_NOSTREAM
from pydantic import BaseModel

from src.providers.anthropic import AnthropicProvider
from src.providers.azure_openai import AzureOpenAIProvider
from src.providers.openai_chat import OpenAIChatProvider
from src.settings import Settings, Tier


class ChatProvider(Protocol):
    @property
    def errors(self) -> type[Exception]: ...

    def model(self, tier: Tier) -> BaseChatModel: ...

    def system(self, prompt: str, hint: str) -> SystemMessage: ...

    def with_tools(
        self, model: BaseChatModel, tools: Sequence[BaseTool], final: bool
    ) -> Runnable[Any, Any]: ...

    def structured(self, model: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, Any]: ...


def make_provider(settings: Settings) -> ChatProvider | None:
    match settings.model_provider:
        case "anthropic":
            return AnthropicProvider(settings) if settings.anthropic_key() else None
        case "openai_chat":
            return OpenAIChatProvider(settings) if settings.openai_key() else None
        case "azure_openai":
            configured = (
                settings.azure_key()
                and settings.azure_openai_endpoint.strip()
                and settings.azure_openai_api_version.strip()
            )
            return AzureOpenAIProvider(settings) if configured else None


def quiet(runnable: Runnable[Any, Any]) -> Runnable[Any, Any]:
    return runnable.with_config(tags=[TAG_NOSTREAM])


async def structured_call[T: BaseModel](
    provider: ChatProvider, model: BaseChatModel, schema: type[T], messages: Sequence[BaseMessage]
) -> T:
    result = await quiet(provider.structured(model, schema)).ainvoke(list(messages))
    if not isinstance(result, schema):
        raise TypeError(f"Expected {schema.__name__}, got {type(result).__name__}.")
    return result
