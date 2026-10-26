import asyncio
import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from q_agent.auth import CurrentUser, get_current_user
from q_agent.conversations import graph as graph_module
from q_agent.conversations.graph import PLACEHOLDER
from q_agent.main import app
from q_agent.settings import get_settings

USER_HEADER = "x-test-uid"


def fake_current_user(request: Request) -> CurrentUser:
    return CurrentUser(uid=UUID(request.headers[USER_HEADER]), oid="test-oid")


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgres:17-alpine") as postgres:
        yield postgres.get_connection_url().replace("+psycopg2", "")


@pytest.fixture(scope="module")
def client(database_url: str) -> Iterator[TestClient]:
    patch = pytest.MonkeyPatch()
    patch.setenv("DATABASE_URL", database_url)
    patch.setenv("JWKS_URL", "http://unused/jwks.json")
    patch.setattr(graph_module, "WORD_DELAY_SECONDS", 0)
    get_settings.cache_clear()
    app.dependency_overrides[get_current_user] = fake_current_user
    try:
        with TestClient(
            app, backend_options={"loop_factory": asyncio.SelectorEventLoop}
        ) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        get_settings.cache_clear()
        patch.undo()


@pytest.fixture
def user_a() -> dict[str, str]:
    return {USER_HEADER: str(uuid4())}


@pytest.fixture
def user_b() -> dict[str, str]:
    return {USER_HEADER: str(uuid4())}


def create(client: TestClient, headers: dict[str, str], text: str) -> dict[str, Any]:
    response = client.post("/conversations", json={"text": text}, headers=headers)
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


def parse_frames(body: str) -> list[tuple[str, Any]]:
    frames: list[tuple[str, Any]] = []
    for block in body.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        frames.append((event, json.loads(data)))
    return frames


def test_list_is_newest_first(client: TestClient, user_a: dict[str, str]) -> None:
    create(client, user_a, "first")
    create(client, user_a, "second")

    response = client.get("/conversations", headers=user_a)

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["second", "first"]


def test_create_sets_title_and_returns_camel_case_item(
    client: TestClient, user_a: dict[str, str]
) -> None:
    text = "  plan a relaxing week long trip through the mountains of northern Vietnam  "

    item = create(client, user_a, text)

    assert item["title"] == "plan a relaxing week long trip through the…"
    assert set(item) == {"id", "title", "createdAt"}


def test_other_user_gets_404_and_empty_list(
    client: TestClient, user_a: dict[str, str], user_b: dict[str, str]
) -> None:
    conversation_id = create(client, user_a, "private")["id"]

    get_response = client.get(f"/conversations/{conversation_id}", headers=user_b)
    send_response = client.post(
        f"/conversations/{conversation_id}/messages", json={"text": "hi"}, headers=user_b
    )
    list_response = client.get("/conversations", headers=user_b)

    assert get_response.status_code == 404
    assert get_response.headers["content-type"].startswith("application/problem+json")
    assert send_response.status_code == 404
    assert list_response.json() == {"items": []}
    assert client.get(f"/conversations/{conversation_id}", headers=user_a).status_code == 200


def test_unknown_conversation_is_404(client: TestClient, user_a: dict[str, str]) -> None:
    assert client.get(f"/conversations/{uuid4()}", headers=user_a).status_code == 404


def test_malformed_conversation_id_is_404(client: TestClient, user_a: dict[str, str]) -> None:
    get_response = client.get("/conversations/garbage", headers=user_a)
    send_response = client.post(
        "/conversations/garbage/messages", json={"text": "hi"}, headers=user_a
    )

    assert get_response.status_code == 404
    assert get_response.headers["content-type"].startswith("application/problem+json")
    assert send_response.status_code == 404


@pytest.mark.parametrize("text", ["", "   \n\t "])
def test_empty_text_is_422(client: TestClient, user_a: dict[str, str], text: str) -> None:
    conversation_id = create(client, user_a, "real")["id"]

    create_response = client.post("/conversations", json={"text": text}, headers=user_a)
    send_response = client.post(
        f"/conversations/{conversation_id}/messages", json={"text": text}, headers=user_a
    )

    assert create_response.status_code == 422
    assert send_response.status_code == 422


def test_new_conversation_has_no_messages(client: TestClient, user_a: dict[str, str]) -> None:
    item = create(client, user_a, "fresh")

    response = client.get(f"/conversations/{item['id']}", headers=user_a)

    assert response.json() == {**item, "messages": []}


def test_send_streams_tokens_then_done_and_turn_is_readable(
    client: TestClient, user_a: dict[str, str]
) -> None:
    conversation_id = create(client, user_a, "hello there")["id"]

    response = client.post(
        f"/conversations/{conversation_id}/messages", json={"text": "hello there"}, headers=user_a
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = parse_frames(response.text)
    assert [event for event, _ in frames[:-1]] == ["token"] * (len(frames) - 1)
    assert len(frames) > 2
    assert frames[-1] == ("done", {})
    assert "".join(data["text"] for _, data in frames[:-1]) == PLACEHOLDER

    detail = client.get(f"/conversations/{conversation_id}", headers=user_a).json()
    assert detail["messages"] == [
        {"role": "user", "text": "hello there"},
        {"role": "assistant", "text": PLACEHOLDER},
    ]


def test_second_turn_appends_to_the_thread(client: TestClient, user_a: dict[str, str]) -> None:
    conversation_id = create(client, user_a, "one")["id"]

    for text in ("one", "two"):
        response = client.post(
            f"/conversations/{conversation_id}/messages", json={"text": text}, headers=user_a
        )
        assert response.status_code == 200

    detail = client.get(f"/conversations/{conversation_id}", headers=user_a).json()
    assert [message["text"] for message in detail["messages"]] == [
        "one",
        PLACEHOLDER,
        "two",
        PLACEHOLDER,
    ]
