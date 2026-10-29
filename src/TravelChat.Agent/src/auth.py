from functools import lru_cache
from typing import Annotated, Any, Protocol
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from src.settings import Settings, get_settings

REQUIRED_CLAIMS = ["exp", "iss", "aud", "sub", "uid"]
JWKS_TIMEOUT_SECONDS = 3

bearer_scheme = HTTPBearer(auto_error=False)


class SigningKey(Protocol):
    @property
    def key(self) -> Any: ...


class SigningKeyProvider(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


class CurrentUser(BaseModel, frozen=True):
    uid: UUID
    oid: str


@lru_cache
def _build_jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(jwks_url, timeout=JWKS_TIMEOUT_SECONDS)


def get_jwks_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SigningKeyProvider:
    return _build_jwks_client(settings.jwks_url)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Could not validate credentials",
        {"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    jwks_client: Annotated[SigningKeyProvider, Depends(get_jwks_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentUser:
    if credentials is None:
        raise _unauthorized()
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(credentials.credentials)
        claims = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.audience,
            issuer=settings.issuer,
            options={"require": REQUIRED_CLAIMS},
        )
        uid = claims["uid"]
        if not isinstance(uid, str):
            raise _unauthorized()
        return CurrentUser(uid=UUID(uid), oid=claims["sub"])
    except jwt.PyJWTError, ValueError:
        raise _unauthorized() from None


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
