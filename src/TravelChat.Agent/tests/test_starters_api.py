import asyncio
from collections.abc import Iterator
from datetime import date, datetime
from functools import partial
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from src import main as main_module
from src.auth import CurrentUser, get_current_user
from src.main import app
from src.settings import Settings, get_settings
from src.starters.pills import starter_texts
from src.starters.repository import SummaryRow, upsert_summary
from src.utils.tools import Tools

USER_HEADER = "x-test-uid"
NOW = datetime(2026, 1, 1)


def row(
    destination: str = "Tokyo",
    *,
    start_date: date | None = None,
    trip_type: str | None = None,
    preferences: list[str] | None = None,
) -> SummaryRow:
    return SummaryRow(
        conversation_id=uuid4(),
        destination=destination,
        start_date=start_date,
        end_date=None,
        trip_type=trip_type,
        preferences=preferences or [],
        updated_at=NOW,
    )


def test_example_text_with_type_month_and_preferences() -> None:
    rows = [
        row(
            "Tokyo",
            start_date=date(2026, 5, 10),
            trip_type="food",
            preferences=["ramen", "sushi", "markets"],
        )
    ]

    assert starter_texts(rows) == [
        "Plan another food trip to Tokyo in May",
        "Find a trip like Tokyo for ramen, sushi and markets",
    ]


def test_distinct_destinations_max_three_case_insensitive() -> None:
    rows = [row("Tokyo"), row("tokyo"), row("Rome"), row("Oslo"), row("Lima")]

    texts = starter_texts(rows)

    assert texts == [
        "Plan another trip to Tokyo",
        "Plan another trip to Rome",
        "Plan another trip to Oslo",
        "Find a trip like Tokyo",
    ]


def test_missing_parts_are_left_out() -> None:
    assert starter_texts([row("Rome")]) == [
        "Plan another trip to Rome",
        "Find a trip like Rome",
    ]


def test_two_preferences_join_with_and() -> None:
    texts = starter_texts([row("Rome", preferences=["art", "wine"])])

    assert texts[1] == "Find a trip like Rome for art and wine"


def test_one_preference_has_no_connector() -> None:
    texts = starter_texts([row("Rome", preferences=["art"])])

    assert texts[1] == "Find a trip like Rome for art"


def test_never_more_than_four() -> None:
    rows = [row(name, trip_type="food") for name in ("A", "B", "C", "D", "E")]

    assert len(starter_texts(rows)) == 4


def test_like_line_uses_newest_row_only() -> None:
    rows = [
        row("Tokyo", preferences=["ramen"]),
        row("Rome", preferences=["art"]),
    ]

    texts = starter_texts(rows)

    assert "Find a trip like Tokyo for ramen" in texts
    assert not any("like Rome" in text for text in texts)


@pytest.mark.parametrize(
    ("trip_type", "expected"),
    [
        ("Food Trip", "Plan another Food Trip to Rome"),
        ("food tour", "Plan another food tour to Rome"),
        ("beach getaway", "Plan another beach getaway to Rome"),
        ("hiking", "Plan another hiking trip to Rome"),
        ("trip", "Plan another trip to Rome"),
        (None, "Plan another trip to Rome"),
    ],
)
def test_trip_noun_is_not_doubled(trip_type: str | None, expected: str) -> None:
    texts = starter_texts([row("Rome", trip_type=trip_type)])

    assert texts[0] == expected


def test_no_rows_gives_no_texts() -> None:
    assert starter_texts([]) == []


async def fake_load_tools(settings: Settings) -> Tools:
    return Tools()


def fake_make_provider(settings: Settings) -> None:
    return None


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
    patch.setattr(main_module, "make_provider", fake_make_provider)
    patch.setattr(main_module, "load_tools", fake_load_tools)
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


def remember(
    client: TestClient,
    headers: dict[str, str],
    destination: str,
    *,
    start_date: str | None = None,
    trip_type: str | None = None,
    preferences: list[str] | None = None,
) -> str:
    created = client.post(
        "/conversations", json={"text": f"Trip to {destination}"}, headers=headers
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    assert client.portal is not None
    saved = client.portal.call(
        partial(
            upsert_summary,
            client.app.state.pool,  # type: ignore[attr-defined]
            UUID(conversation_id),
            UUID(headers[USER_HEADER]),
            destination=destination,
            start_date=start_date,
            end_date=None,
            trip_type=trip_type,
            preferences=preferences or [],
        )
    )
    assert saved
    return conversation_id


def starters(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get("/conversations/starters", headers=headers)
    assert response.status_code == 200
    return response.json()


def conversation_ids(client: TestClient, headers: dict[str, str]) -> set[str]:
    response = client.get("/conversations", headers=headers)
    assert response.status_code == 200
    return {item["id"] for item in response.json()["items"]}


def test_fresh_user_is_enabled_with_no_items(client: TestClient, user_a: dict[str, str]) -> None:
    assert starters(client, user_a) == {"enabled": True, "items": []}


def test_items_follow_remembered_trips(client: TestClient, user_a: dict[str, str]) -> None:
    remember(
        client,
        user_a,
        "Tokyo",
        start_date="2026-05-10",
        trip_type="food",
        preferences=["ramen"],
    )

    assert starters(client, user_a) == {
        "enabled": True,
        "items": [
            "Plan another food trip to Tokyo in May",
            "Find a trip like Tokyo for ramen",
        ],
    }


def test_disabled_returns_no_items_and_can_be_re_enabled(
    client: TestClient, user_a: dict[str, str]
) -> None:
    remember(client, user_a, "Tokyo")

    off = client.put("/conversations/starters/setting", json={"enabled": False}, headers=user_a)

    assert off.status_code == 200
    assert off.json() == {"enabled": False}
    assert starters(client, user_a) == {"enabled": False, "items": []}
    on = client.put("/conversations/starters/setting", json={"enabled": True}, headers=user_a)
    assert on.json() == {"enabled": True}
    assert starters(client, user_a)["items"]


@pytest.mark.parametrize("body", [{"enabled": "yes"}, {"enabled": 1}, {}])
def test_setting_rejects_a_non_bool(
    client: TestClient, user_a: dict[str, str], body: dict[str, Any]
) -> None:
    response = client.put("/conversations/starters/setting", json=body, headers=user_a)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 422


def test_setting_rejects_a_missing_body(client: TestClient, user_a: dict[str, str]) -> None:
    response = client.put("/conversations/starters/setting", headers=user_a)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_delete_clears_the_callers_rows_and_keeps_conversations(
    client: TestClient, user_a: dict[str, str]
) -> None:
    remember(client, user_a, "Tokyo")
    remember(client, user_a, "Rome")
    before = conversation_ids(client, user_a)

    response = client.delete("/conversations/starters/history", headers=user_a)

    assert response.status_code == 204
    assert response.content == b""
    assert starters(client, user_a) == {"enabled": True, "items": []}
    assert conversation_ids(client, user_a) == before


def test_setting_and_history_are_owner_scoped(
    client: TestClient, user_a: dict[str, str], user_b: dict[str, str]
) -> None:
    remember(client, user_a, "Tokyo")
    remember(client, user_b, "Rome")

    client.put("/conversations/starters/setting", json={"enabled": False}, headers=user_a)

    assert starters(client, user_a) == {"enabled": False, "items": []}
    assert starters(client, user_b)["enabled"] is True
    assert starters(client, user_b)["items"][0] == "Plan another trip to Rome"


def test_delete_leaves_other_users_rows(
    client: TestClient, user_a: dict[str, str], user_b: dict[str, str]
) -> None:
    remember(client, user_a, "Tokyo")
    remember(client, user_b, "Rome")

    assert client.delete("/conversations/starters/history", headers=user_a).status_code == 204

    assert starters(client, user_a)["items"] == []
    assert starters(client, user_b)["items"][0] == "Plan another trip to Rome"


def test_only_the_newest_five_rows_are_used(client: TestClient, user_a: dict[str, str]) -> None:
    remember(client, user_a, "Rome")
    for name in ("oslo", "OSLO", "oslo ", "oslo", "Oslo"):
        remember(client, user_a, name)

    items = starters(client, user_a)["items"]

    assert items == ["Plan another trip to Oslo", "Find a trip like Oslo"]


def test_starters_path_is_not_parsed_as_a_conversation_id(
    client: TestClient, user_a: dict[str, str]
) -> None:
    response = client.get("/conversations/starters", headers=user_a)

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "items": []}
