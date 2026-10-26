from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwks_url: str
    issuer: str = "q-gateway"
    audience: str = "q-internal"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields are loaded from the environment
