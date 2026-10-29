from functools import lru_cache
from typing import Any, Literal

from pydantic import SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

type Provider = Literal["anthropic", "openai_chat", "azure_openai"]
type Tier = Literal["low", "medium", "high"]

TIERS: tuple[Tier, ...] = ("low", "medium", "high")
DEFAULT_MCP_URL = "http://q-mcp:8080/mcp"
OPENAI_DEFAULT_MODEL = "gpt-5.4-mini"
PROVIDER_MODELS: dict[Provider, dict[Tier, str]] = {
    "anthropic": {
        "low": "claude-haiku-5-5",
        "medium": "claude-sonnet-5-5",
        "high": "claude-opus-5-5",
    },
    "openai_chat": dict.fromkeys(TIERS, OPENAI_DEFAULT_MODEL),
    "azure_openai": dict.fromkeys(TIERS, OPENAI_DEFAULT_MODEL),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(protected_namespaces=())

    database_url: str
    jwks_url: str
    issuer: str = "q-gateway"
    audience: str = "q-internal"

    model_provider: Provider = "openai_chat"
    model_low: str = ""
    model_medium: str = ""
    model_high: str = ""
    answer_tier: Tier = "medium"
    title_tier: Tier = "low"

    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_base_url: str = ""
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_api_version: str = ""

    mcp_url: str = DEFAULT_MCP_URL

    @field_validator(
        "model_provider",
        "model_low",
        "model_medium",
        "model_high",
        "answer_tier",
        "title_tier",
        "anthropic_base_url",
        "openai_base_url",
        "azure_openai_endpoint",
        "azure_openai_api_version",
        "mcp_url",
        mode="before",
    )
    @classmethod
    def empty_means_default(cls, value: Any, info: ValidationInfo) -> Any:
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[str(info.field_name)].default
        return value

    def model_for(self, tier: Tier) -> str:
        configured = {
            "low": self.model_low,
            "medium": self.model_medium,
            "high": self.model_high,
        }[tier].strip()
        return configured or PROVIDER_MODELS[self.model_provider][tier]

    def anthropic_key(self) -> str:
        return self.anthropic_api_key.get_secret_value().strip()

    def openai_key(self) -> str:
        return self.openai_api_key.get_secret_value().strip()

    def azure_key(self) -> str:
        return self.azure_openai_api_key.get_secret_value().strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields are loaded from the environment
