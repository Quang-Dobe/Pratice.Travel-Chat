import asyncio
import json
from collections.abc import Iterator, Sequence
from datetime import timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import anthropic
import httpx2
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    SystemMessage,
)
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel
from testcontainers.community.postgres import PostgresContainer

from src import main as main_module
from src.auth import CurrentUser, get_current_user
from src.graph.classify import Classification
from src.graph.deps import PlannerDeps
from src.graph.suggest import Pills
from src.main import app
from src.settings import Settings, Tier, get_settings
from src.utils.modules import load
from src.utils.tools import Tools, calculator, time_tool, with_events
from src.utils.trip import today

USER_HEADER = "x-test-uid"
ANSWER = "It is sunny in Tokyo."
TITLE = "Tokyo weather"
PILLS: list[dict[str, Any]] = [
    {"text": "Plan a trip", "action": None},
    {"text": "Check flights", "action": None},
]
CARD_PILLS: list[dict[str, Any]] = [
    {"text": "Approve", "action": "approve"},
    {"text": "Reject", "action": "reject"},
    *PILLS,
]
WEATHER_CALL = {"name": "weather__forecast", "kind": "retrieve"}
extract_module = load("extract")
Details = extract_module.Details
Extraction = extract_module.Extraction
type Turn = tuple[Classification, Any]

DETAIL_FIELDS = (
    "destination",
    "origin",
    "start_date",
    "end_date",
    "length_days",
    "travellers",
    "budget_level",
    "budget_usd",
    "interests",
    "flight_criteria",
    "hotel_criteria",
)
itinerary_module = load("draft_itinerary")


def listing(prefix: str, label: str, base: float) -> str:
    return json.dumps(
        [
            {
                "id": f"{prefix}{index}",
                "summary": f"{label} {index}",
                "priceUsd": base + index,
                "deepLink": f"https://{label.lower()}.example/{prefix}{index}",
            }
            for index in range(1, 5)
        ]
    )


FLIGHTS = listing("f", "Flight", 400.0)
HOTELS = listing("h", "Hotel", 600.0)


class FakeChat(BaseChatModel):
    script: Any = None

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _next(self) -> AIMessage:
        if not self.script:
            raise RuntimeError("no scripted reply")
        reply = self.script.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return cast("AIMessage", reply)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        reply = self._next()
        chunks = [
            tool_call_chunk(
                name=call["name"], args=json.dumps(call["args"]), id=call["id"], index=i
            )
            for i, call in enumerate(reply.tool_calls)
        ]
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=reply.content, tool_call_chunks=chunks)
        )


def classification(
    *,
    route: Literal["answer", "plan"] = "answer",
    verdict: Literal["travel", "weather", "off_topic"] = "weather",
    change: Literal["flights", "hotel"] | None = None,
    budget_focus: Literal["none", "hotels", "flights", "places"] = "none",
    **details: Any,
) -> Turn:
    fields: dict[str, Any] = dict.fromkeys(DETAIL_FIELDS)
    fields.update(details)
    extraction = (
        Extraction(brief=Details(**fields), change=change or "none", budget_focus=budget_focus)
        if route == "plan"
        else None
    )
    return Classification(verdict=verdict, route=route), extraction


def queue_turns(queues: dict[str, list[Any]], *turns: Turn) -> None:
    queues["Classification"] = [turn[0] for turn in turns]
    queues["Extraction"] = [turn[1] for turn in turns if turn[1] is not None]


class FakeProvider:
    def __init__(self) -> None:
        self.model_calls: list[str] = []
        self.scripts: dict[str, list[Any]] = {}
        self.queues: dict[str, list[Any]] = {}
        self.reset()

    def reset(self) -> None:
        keys = ("low", "medium", "answer", "weather", "places", "flights", "hotels")
        self.scripts = {key: [] for key in keys}
        self.scripts["low"].append(AIMessage(TITLE))
        days = [itinerary_module.DayDraft(date="", activities=["Tsukiji market", "Senso-ji"])] * 5
        self.queues = {
            "Pills": [Pills(items=["Plan a trip", "Check flights"])],
            "ItineraryDraft": [itinerary_module.ItineraryDraft(days=days)],
        }
        queue_turns(self.queues, classification())
        self.model_calls = []

    @property
    def errors(self) -> type[Exception]:
        return anthropic.APIError

    def model(self, tier: Tier) -> BaseChatModel:
        self.model_calls.append(tier)
        return FakeChat(script=self.scripts["low" if tier == "low" else "medium"])

    def system(self, prompt: str, hint: str) -> SystemMessage:
        return SystemMessage(f"{prompt}\n{hint}")

    def with_tools(
        self, model: BaseChatModel, tools: Sequence[BaseTool], final: bool
    ) -> Runnable[Any, Any]:
        name = tools[0].name
        key = name.split("__")[0] if "__" in name else "answer"
        return FakeChat(script=self.scripts[key])

    def structured(self, model: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, Any]:
        name = schema.__name__

        def pick(_: Any) -> BaseModel:
            queue = self.queues[name]
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return cast("BaseModel", item)

        return RunnableLambda(pick)


PROVIDER = FakeProvider()


def make_fake_provider(settings: Settings) -> FakeProvider:
    return PROVIDER


async def forecast(city: str, startDate: str = "", endDate: str = "") -> str:
    day = {
        "date": (today() + timedelta(days=14)).isoformat(),
        "highC": 20,
        "lowC": 12,
        "rainChancePct": 30,
    }
    return json.dumps({"city": city, "days": [day]})


async def attractions(city: str, interests: list[str] | None = None) -> str:
    return json.dumps([{"name": "Senso-ji", "category": "temple"}])


async def flights_search(
    origin: str, destination: str, departDate: str, returnDate: str, travellers: int = 1
) -> str:
    return FLIGHTS


async def hotels_search(city: str, checkIn: str, checkOut: str, travellers: int = 1) -> str:
    return HOTELS


def data_tool(function: Any, name: str) -> BaseTool:
    tool = StructuredTool.from_function(coroutine=function, name=name, description=name)
    return with_events(tool)


async def fake_load_tools(settings: Settings) -> Tools:
    return Tools(
        local=[with_events(calculator), with_events(time_tool)],
        domains={
            "weather": [data_tool(forecast, "weather__forecast")],
            "places": [data_tool(attractions, "places__attractions")],
            "flights": [data_tool(flights_search, "flights__search")],
            "hotels": [data_tool(hotels_search, "hotels__search")],
        },
    )


def call_reply(name: str, args: dict[str, Any], text: str = "") -> AIMessage:
    call = {"name": name, "args": args, "id": f"call-{uuid4().hex[:8]}"}
    return AIMessage(content=text, tool_calls=[call])


def script_weather_answer(preamble: str = "") -> None:
    PROVIDER.scripts["answer"] += [
        call_reply("weather_agent", {"request": "Tokyo"}, text=preamble),
        AIMessage(ANSWER),
    ]
    PROVIDER.scripts["weather"] += [
        call_reply("weather__forecast", {"city": "Tokyo"}),
        AIMessage("Sunny."),
    ]


def script_demo() -> None:
    queue_turns(
        PROVIDER.queues,
        classification(
            route="plan",
            verdict="travel",
            destination="Tokyo",
            origin="Singapore",
            length_days=3,
            interests=["food"],
        ),
    )
    flights = {"origin": "SIN", "destination": "TYO", "departDate": "d", "returnDate": "r"}
    PROVIDER.scripts["flights"] += [
        call_reply("flights__search", flights),
        AIMessage("Nonstop is cheapest."),
    ]
    stay = {"city": "Tokyo", "checkIn": "a", "checkOut": "b"}
    PROVIDER.scripts["hotels"] += [call_reply("hotels__search", stay), AIMessage("Central.")]
    PROVIDER.scripts["weather"] += [
        call_reply("weather__forecast", {"city": "Tokyo", "startDate": "a", "endDate": "b"}),
        AIMessage("Mild."),
    ]
    PROVIDER.scripts["places"] += [
        call_reply("places__attractions", {"city": "Tokyo"}),
        AIMessage("Temples."),
    ]


@pytest.fixture(autouse=True)
def reset_fakes() -> None:
    PROVIDER.reset()


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
    patch.setattr(main_module, "make_provider", make_fake_provider)
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


def events_of(frames: list[tuple[str, Any]]) -> list[str]:
    return [event for event, _ in frames]


def tokens_of(frames: list[tuple[str, Any]]) -> list[str]:
    return [data["text"] for event, data in frames if event == "token"]


def user_item(text: str) -> dict[str, Any]:
    return {"role": "user", "text": text, "toolCalls": [], "planCard": None, "pills": []}


def assistant_item(
    text: str,
    calls: list[dict[str, str]] | None = None,
    pills: list[dict[str, Any]] | None = None,
    card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "text": text,
        "toolCalls": calls or [],
        "planCard": card,
        "pills": pills or [],
    }


def send(
    client: TestClient,
    headers: dict[str, str],
    conversation_id: str,
    text: str,
    action: dict[str, Any] | None = None,
) -> str:
    body: dict[str, Any] = {"text": text}
    if action is not None:
        body["action"] = action
    response = client.post(f"/conversations/{conversation_id}/messages", json=body, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return response.text


def detail_of(client: TestClient, headers: dict[str, str], conversation_id: str) -> dict[str, Any]:
    response = client.get(f"/conversations/{conversation_id}", headers=headers)
    body: dict[str, Any] = response.json()
    return body


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


@pytest.mark.parametrize(
    "action",
    [
        {"type": "cancel"},
        {"type": "approve"},
        {"type": "approve", "cardId": "c1", "flightId": "f1"},
        {"type": "approve", "cardId": "c1", "hotelId": "h1"},
        {"type": "approve", "flightId": "f1", "hotelId": "h1"},
        {"type": "approve", "cardId": "", "flightId": "f1", "hotelId": "h1"},
        {},
    ],
)
def test_an_unknown_action_or_an_approve_without_its_ids_is_422(
    client: TestClient, user_a: dict[str, str], action: dict[str, Any]
) -> None:
    conversation_id = create(client, user_a, "trip")["id"]

    response = client.post(
        f"/conversations/{conversation_id}/messages",
        json={"text": "Approve", "action": action},
        headers=user_a,
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert detail_of(client, user_a, conversation_id)["messages"] == []


def test_the_approvals_route_is_gone(client: TestClient, user_a: dict[str, str]) -> None:
    conversation_id = create(client, user_a, "trip")["id"]

    response = client.post(
        f"/conversations/{conversation_id}/approvals",
        json={"approvalId": "x", "optionId": "f1"},
        headers=user_a,
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_send_streams_tool_calls_tokens_pills_then_done_and_turn_is_readable(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_weather_answer()
    conversation_id = create(client, user_a, "weather in Tokyo")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "weather in Tokyo"))

    events = events_of(frames)
    assert events[0] == "tool_call"
    assert events[-2:] == ["pills", "done"]
    assert set(events) == {"tool_call", "token", "pills", "done"}
    assert frames[0][1] == {"kind": "retrieve", "name": "weather__forecast", "status": "started"}
    assert "".join(tokens_of(frames)) == ANSWER
    assert frames[-2][1] == {"items": PILLS}
    assert frames[-1] == ("done", {})
    detail = detail_of(client, user_a, conversation_id)
    assert detail["messages"] == [
        user_item("weather in Tokyo"),
        assistant_item(ANSWER, calls=[WEATHER_CALL], pills=PILLS),
    ]
    assert "pendingApprovalId" not in detail


def test_text_from_two_model_calls_is_one_bubble_live_and_reloaded(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_weather_answer(preamble="Let me check.")
    conversation_id = create(client, user_a, "weather in Tokyo")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "weather in Tokyo"))

    tokens = tokens_of(frames)
    expected = f"Let me check.\n\n{ANSWER}"
    assert tokens == ["Let me check.", "\n\n", ANSWER]
    assert "".join(tokens) == expected
    detail = detail_of(client, user_a, conversation_id)
    assert detail["messages"] == [
        user_item("weather in Tokyo"),
        assistant_item(expected, calls=[WEATHER_CALL], pills=PILLS),
    ]


def test_title_is_replaced_after_the_first_turn_only(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["answer"] += [AIMessage("First."), AIMessage("Second.")]
    conversation_id = create(client, user_a, "weather in Tokyo please")["id"]

    send(client, user_a, conversation_id, "weather in Tokyo please")
    send(client, user_a, conversation_id, "and tomorrow")

    detail = detail_of(client, user_a, conversation_id)
    assert detail["title"] == TITLE
    assert PROVIDER.model_calls.count("low") == 1
    listed = client.get("/conversations", headers=user_a).json()["items"]
    assert [item["title"] for item in listed] == [TITLE]


def test_second_turn_appends_to_the_thread(client: TestClient, user_a: dict[str, str]) -> None:
    PROVIDER.scripts["answer"] += [AIMessage("First."), AIMessage("Second.")]
    conversation_id = create(client, user_a, "one")["id"]

    for text in ("one", "two"):
        send(client, user_a, conversation_id, text)

    detail = detail_of(client, user_a, conversation_id)
    assert [message["text"] for message in detail["messages"]] == [
        "one",
        "First.",
        "two",
        "Second.",
    ]


def test_without_a_model_key_the_turn_gets_a_notice_and_is_not_saved(
    client: TestClient, user_a: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    unconfigured = PlannerDeps(provider=None, settings=get_settings())
    monkeypatch.setattr(app.state, "planner", unconfigured)
    conversation_id = create(client, user_a, "hello")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "hello"))

    assert frames == [
        ("notice", {"message": "The AI model is not configured."}),
        ("done", {}),
    ]
    assert detail_of(client, user_a, conversation_id)["messages"] == []


def test_unexpected_failure_ends_with_a_notice_then_done(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.queues["Classification"] = [RuntimeError("boom")]
    conversation_id = create(client, user_a, "weather in Tokyo")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "weather in Tokyo"))

    assert frames[-2] == ("notice", {"message": "The AI model is unavailable right now."})
    assert frames[-1] == ("done", {})


def test_model_connection_error_ends_with_a_notice_then_done(
    client: TestClient, user_a: dict[str, str]
) -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    PROVIDER.queues["Classification"] = [anthropic.APIConnectionError(request=request)]
    conversation_id = create(client, user_a, "weather in Tokyo")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "weather in Tokyo"))

    assert frames[-2] == ("notice", {"message": "The AI model is unavailable right now."})
    assert frames[-1] == ("done", {})


def test_a_plan_turn_streams_the_card_and_the_detail_rebuilds_it_with_pills(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_demo()
    conversation_id = create(client, user_a, "3 days in Tokyo from Singapore")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "3 days in Tokyo from Singapore"))

    events = events_of(frames)
    assert events[0] == "tool_call"
    assert events[-3:] == ["plan_card", "pills", "done"]
    card = frames[-3][1]
    assert set(card) == {"id", "status", "flights", "hotels", "picked", "days", "budget"}
    assert card["status"] == "open"
    assert [option["id"] for option in card["flights"]] == ["f1", "f2", "f3"]
    assert card["flights"][0] == {
        "id": "f1",
        "summary": "Flight 1",
        "priceUsd": 401.0,
        "deepLink": "https://flight.example/f1",
    }
    assert [option["id"] for option in card["hotels"]] == ["h1", "h2", "h3"]
    assert card["picked"] == {"flightId": "f1", "hotelId": "h1"}
    assert [day["costUsd"] for day in card["days"]] == [90.0, 90.0, 90.0]
    assert card["days"][0]["weather"] == "12 to 20 °C, 30% chance of rain"
    assert card["budget"] == {
        "flightUsd": 401.0,
        "hotelUsd": 601.0,
        "dailyUsd": 270.0,
        "totalUsd": 1272.0,
        "budgetUsd": None,
        "overBudget": False,
    }
    assert frames[-2][1] == {"items": CARD_PILLS}
    text = "".join(tokens_of(frames))
    assert text.startswith("Here is your 3-day plan for Tokyo.")
    detail = detail_of(client, user_a, conversation_id)
    calls = [
        {"name": "flights__search", "kind": "retrieve"},
        {"name": "hotels__search", "kind": "retrieve"},
        {"name": "weather__forecast", "kind": "retrieve"},
        {"name": "places__attractions", "kind": "retrieve"},
        {"name": "calculator", "kind": "calculate"},
    ]
    assert detail["messages"] == [
        user_item("3 days in Tokyo from Singapore"),
        assistant_item(text, calls=calls, pills=CARD_PILLS, card=card),
    ]


def test_approve_confirms_the_picks_and_the_old_card_reloads_as_approved(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_demo()
    conversation_id = create(client, user_a, "3 days in Tokyo from Singapore")["id"]
    first = parse_frames(send(client, user_a, conversation_id, "3 days in Tokyo from Singapore"))
    card = first[-3][1]
    action = {"type": "approve", "cardId": card["id"], "flightId": "f2", "hotelId": "h3"}

    frames = parse_frames(send(client, user_a, conversation_id, "Approve", action))

    assert events_of(frames) == ["token", "pills", "done"]
    text = "".join(tokens_of(frames))
    assert "https://flight.example/f2" in text
    assert "https://hotel.example/h3" in text
    assert frames[1][1] == {"items": PILLS}
    detail = detail_of(client, user_a, conversation_id)
    first_turn, approve_click, confirmation = detail["messages"][1:]
    assert first_turn["planCard"]["id"] == card["id"]
    assert first_turn["planCard"]["status"] == "approved"
    assert first_turn["planCard"]["picked"] == {"flightId": "f2", "hotelId": "h3"}
    assert first_turn["pills"] == []
    assert approve_click == user_item("Approve")
    assert confirmation["planCard"] is None
    assert confirmation["text"] == text
    assert confirmation["pills"] == PILLS


def test_reject_needs_no_ids_and_asks_what_to_change(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["medium"].append(AIMessage("What would you like to change?"))
    conversation_id = create(client, user_a, "trip")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reject", {"type": "reject"}))

    assert "".join(tokens_of(frames)) == "What would you like to change?"
    assert events_of(frames)[-2:] == ["pills", "done"]
    detail = detail_of(client, user_a, conversation_id)
    assert [message["text"] for message in detail["messages"]] == [
        "Reject",
        "What would you like to change?",
    ]


def test_an_approve_with_no_plan_gets_the_note_and_no_card(
    client: TestClient, user_a: dict[str, str]
) -> None:
    conversation_id = create(client, user_a, "trip")["id"]
    action = {"type": "approve", "cardId": "ghost", "flightId": "f1", "hotelId": "h1"}

    frames = parse_frames(send(client, user_a, conversation_id, "Approve", action))

    assert "plan_card" not in events_of(frames)
    assert "".join(tokens_of(frames)).startswith("There is no plan to approve yet.")
