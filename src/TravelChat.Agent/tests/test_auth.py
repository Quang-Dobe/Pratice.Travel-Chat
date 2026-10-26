import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from q_agent.auth import CurrentUserDep, SigningKey, get_jwks_client
from q_agent.main import http_exception_handler, validation_exception_handler
from q_agent.settings import Settings, get_settings

ISSUER = "q-gateway"
AUDIENCE = "q-internal"
OID = "11111111-aaaa-bbbb-cccc-222222222222"

SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass(frozen=True)
class FakeSigningKey:
    key: Any


class FakeJwksClient:
    def get_signing_key_from_jwt(self, token: str) -> SigningKey:
        return FakeSigningKey(SIGNING_KEY.public_key())


def build_app() -> FastAPI:
    app = FastAPI()
    app.exception_handler(StarletteHTTPException)(http_exception_handler)
    app.exception_handler(RequestValidationError)(validation_exception_handler)

    @app.get("/count")
    def count(n: int) -> dict[str, int]:
        return {"n": n}

    @app.get("/me")
    def me(user: CurrentUserDep) -> dict[str, str]:
        return {"uid": str(user.uid), "oid": user.oid}

    app.dependency_overrides[get_jwks_client] = FakeJwksClient
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql://unused", jwks_url="http://unused/jwks.json"
    )
    return app


def make_token(
    key: Any = SIGNING_KEY,
    algorithm: str = "RS256",
    **overrides: Any,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": OID,
        "uid": str(uuid4()),
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key, algorithm=algorithm)


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app())


def get_me(client: TestClient, token: str | None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get("/me", headers=headers)


def assert_unauthorized(response: Any) -> None:
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_unauthorized_is_problem_json_with_challenge(client: TestClient) -> None:
    response = get_me(client, None)

    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {
        "type": "about:blank",
        "title": "Unauthorized",
        "status": 401,
        "detail": "Could not validate credentials",
    }


def test_validation_error_is_problem_json(client: TestClient) -> None:
    response = client.get("/count", params={"n": "abc"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 422
    assert body["title"] == "Unprocessable Content"
    assert "n" in body["detail"]


def test_valid_token_returns_user(client: TestClient) -> None:
    uid = uuid4()

    response = get_me(client, make_token(uid=str(uid)))

    assert response.status_code == 200
    assert response.json() == {"uid": str(uid), "oid": OID}


def test_missing_header_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, None))


def test_non_bearer_scheme_is_unauthorized(client: TestClient) -> None:
    response = client.get("/me", headers={"Authorization": f"Basic {make_token()}"})

    assert_unauthorized(response)


def test_garbage_token_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, "not-a-jwt"))


def test_bad_signature_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, make_token(key=OTHER_KEY)))


def test_wrong_audience_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, make_token(aud="someone-else")))


def test_wrong_issuer_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, make_token(iss="someone-else")))


def test_expired_token_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, make_token(exp=int(time.time()) - 600)))


def test_missing_exp_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, make_token(exp=None)))


def test_missing_uid_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, make_token(uid=None)))


def test_missing_sub_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, make_token(sub=None)))


def test_non_guid_uid_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, make_token(uid="not-a-guid")))


def test_alg_none_is_unauthorized(client: TestClient) -> None:
    assert_unauthorized(get_me(client, make_token(key=None, algorithm="none")))


def test_hs256_is_unauthorized(client: TestClient) -> None:
    token = make_token(key="a-shared-secret-that-is-long-enough-for-hs256", algorithm="HS256")

    assert_unauthorized(get_me(client, token))
