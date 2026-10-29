from typing import Any, cast

import anthropic
import openai
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableBinding
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import SecretStr

from src.providers.anthropic import AnthropicProvider
from src.providers.azure_openai import AzureOpenAIProvider
from src.providers.base import ChatProvider, make_provider
from src.providers.openai_chat import OpenAIChatProvider
from src.settings import TIERS, Settings
from src.utils.tools import calculator

HINT = "Today is 2026-10-28."
CACHE_MARK = {"type": "ephemeral"}
AZURE_ENDPOINT = "https://res.openai.azure.com"
BLANK: dict[str, Any] = {
    "model_provider": "openai_chat",
    "model_low": "",
    "model_medium": "",
    "model_high": "",
    "anthropic_api_key": SecretStr(""),
    "anthropic_base_url": "",
    "openai_api_key": SecretStr(""),
    "openai_base_url": "",
    "azure_openai_endpoint": "",
    "azure_openai_api_key": SecretStr(""),
    "azure_openai_api_version": "",
}

type Binding = RunnableBinding[Any, Any]


def settings_with(**values: Any) -> Settings:
    return Settings(
        database_url="postgresql://unused", jwks_url="http://unused", **{**BLANK, **values}
    )


def anthropic_settings(**values: Any) -> Settings:
    return settings_with(
        model_provider="anthropic", anthropic_api_key=SecretStr("sk-anthropic"), **values
    )


def openai_settings(**values: Any) -> Settings:
    key = SecretStr("sk-openai")
    return settings_with(model_provider="openai_chat", openai_api_key=key, **values)


def azure_settings(**values: Any) -> Settings:
    return settings_with(
        model_provider="azure_openai",
        azure_openai_api_key=SecretStr("sk-azure"),
        azure_openai_endpoint=AZURE_ENDPOINT,
        azure_openai_api_version="2025-04-01-preview",
        **values,
    )


@pytest.mark.parametrize(
    "values",
    [
        {"model_provider": "anthropic"},
        {"model_provider": "anthropic", "openai_api_key": SecretStr("only-openai")},
        {"model_provider": "openai_chat"},
        {"model_provider": "openai_chat", "anthropic_api_key": SecretStr("only-anthropic")},
        {"model_provider": ""},
        {"model_provider": "azure_openai", "azure_openai_api_key": SecretStr("k")},
        {
            "model_provider": "azure_openai",
            "azure_openai_api_key": SecretStr("k"),
            "azure_openai_endpoint": AZURE_ENDPOINT,
        },
        {
            "model_provider": "azure_openai",
            "azure_openai_endpoint": AZURE_ENDPOINT,
            "azure_openai_api_version": "2025-04-01-preview",
        },
    ],
)
def test_a_provider_missing_its_key_address_or_version_is_not_configured(
    values: dict[str, Any],
) -> None:
    assert make_provider(settings_with(**values)) is None


def test_the_provider_defaults_to_openai_chat_when_the_setting_is_empty() -> None:
    settings = settings_with(model_provider="", openai_api_key=SecretStr("sk-openai"))

    provider = make_provider(settings)

    assert settings.model_provider == "openai_chat"
    assert isinstance(provider, OpenAIChatProvider)
    assert not isinstance(provider, AzureOpenAIProvider)


def test_each_vendor_builds_its_own_provider() -> None:
    assert isinstance(make_provider(anthropic_settings()), AnthropicProvider)
    assert isinstance(make_provider(openai_settings()), OpenAIChatProvider)
    assert isinstance(make_provider(azure_settings()), AzureOpenAIProvider)


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (
            "anthropic",
            {"low": "claude-haiku-5-5", "medium": "claude-sonnet-5-5", "high": "claude-opus-5-5"},
        ),
        ("openai_chat", dict.fromkeys(TIERS, "gpt-5.4-mini")),
        ("azure_openai", dict.fromkeys(TIERS, "gpt-5.4-mini")),
    ],
)
def test_tier_models_default_by_provider(provider: str, expected: dict[str, str]) -> None:
    settings = settings_with(model_provider=provider)

    assert {tier: settings.model_for(tier) for tier in TIERS} == expected


def test_a_configured_tier_model_overrides_only_that_tier() -> None:
    settings = openai_settings(model_medium=" gpt-5.4 ", model_high="")

    assert settings.model_for("low") == "gpt-5.4-mini"
    assert settings.model_for("medium") == "gpt-5.4"
    assert settings.model_for("high") == "gpt-5.4-mini"


def test_anthropic_models_per_tier_and_error_base() -> None:
    provider = AnthropicProvider(anthropic_settings(model_low="claude-custom"))

    assert provider.errors is anthropic.APIError
    low = cast("ChatAnthropic", provider.model("low"))
    medium = cast("ChatAnthropic", provider.model("medium"))
    assert low.model == "claude-custom"
    assert medium.model == "claude-sonnet-5-5"
    assert medium.max_tokens == 4096


def test_anthropic_marks_the_static_system_prompt_for_caching() -> None:
    provider = AnthropicProvider(anthropic_settings())

    with_hint = provider.system("static prompt", HINT)
    without_hint = provider.system("static prompt", "")

    assert with_hint.content == [
        {"type": "text", "text": "static prompt", "cache_control": CACHE_MARK},
        {"type": "text", "text": HINT},
    ]
    assert without_hint.content == [
        {"type": "text", "text": "static prompt", "cache_control": CACHE_MARK}
    ]


def test_anthropic_final_mode_forbids_tool_calls_with_a_none_dict() -> None:
    provider = AnthropicProvider(anthropic_settings())
    model = provider.model("medium")

    final = cast("Binding", provider.with_tools(model, [calculator], final=True))
    open_ = cast("Binding", provider.with_tools(model, [calculator], final=False))

    assert final.kwargs["tool_choice"] == {"type": "none"}
    assert "tool_choice" not in open_.kwargs
    assert [tool["name"] for tool in final.kwargs["tools"]] == ["calculator"]


def test_openai_chat_models_per_tier_and_error_base() -> None:
    base_url = f"{AZURE_ENDPOINT}/openai/v1"
    provider = OpenAIChatProvider(openai_settings(model_low="gpt-low", openai_base_url=base_url))

    assert provider.errors is openai.APIError
    low = cast("ChatOpenAI", provider.model("low"))
    medium = cast("ChatOpenAI", provider.model("medium"))
    assert low.model_name == "gpt-low"
    assert medium.model_name == "gpt-5.4-mini"
    assert medium.max_tokens == 4096
    assert str(medium.openai_api_base) == base_url


def test_openai_chat_works_without_a_base_url() -> None:
    provider = make_provider(openai_settings())

    assert isinstance(provider, OpenAIChatProvider)
    assert provider.model("medium") is not None


def test_openai_chat_has_no_cache_mark_and_one_system_string() -> None:
    provider = OpenAIChatProvider(openai_settings())

    assert provider.system("static prompt", HINT).content == f"static prompt\n\n{HINT}"
    assert provider.system("static prompt", "").content == "static prompt"


def test_openai_chat_final_mode_forbids_tool_calls_with_none() -> None:
    provider = OpenAIChatProvider(openai_settings())
    model = provider.model("medium")

    final = cast("Binding", provider.with_tools(model, [calculator], final=True))
    open_ = cast("Binding", provider.with_tools(model, [calculator], final=False))

    assert final.kwargs["tool_choice"] == "none"
    assert "tool_choice" not in open_.kwargs


def test_azure_builds_azure_chat_models_on_the_tier_deployment() -> None:
    provider = AzureOpenAIProvider(azure_settings(model_medium="my-deployment"))

    assert provider.errors is openai.APIError
    medium = provider.model("medium")
    assert isinstance(medium, AzureChatOpenAI)
    assert medium.deployment_name == "my-deployment"
    assert isinstance(provider.model("low"), AzureChatOpenAI)


def test_every_provider_satisfies_the_protocol() -> None:
    providers: list[ChatProvider] = [
        AnthropicProvider(anthropic_settings()),
        OpenAIChatProvider(openai_settings()),
        AzureOpenAIProvider(azure_settings()),
    ]

    assert [type(provider).__name__ for provider in providers] == [
        "AnthropicProvider",
        "OpenAIChatProvider",
        "AzureOpenAIProvider",
    ]
