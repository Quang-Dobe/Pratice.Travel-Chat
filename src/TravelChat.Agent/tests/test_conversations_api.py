import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import date, timedelta
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
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel
from testcontainers.community.postgres import PostgresContainer

from src import main as main_module
from src.auth import CurrentUser, get_current_user
from src.conversations.router import PlanCardItem
from src.conversations.router import _build_messages as build_messages
from src.graph.builder import build_graph
from src.graph.classify import Classification
from src.graph.deps import PlannerDeps
from src.graph.refuse import REFUSAL
from src.graph.state import PlanCard
from src.graph.suggest import Pills
from src.graph.summarize import TripSummary
from src.main import app
from src.settings import Settings, Tier, get_settings
from src.starters import repository as starters_repository
from src.utils.modules import load
from src.utils.reviews import PinName, PinNames
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
CARD_KEYS = {"id", "status", "flights", "hotels", "picked", "days", "budget", "reviewPins"}
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
    delay: float = 0.0

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
        reply = self._next()
        time.sleep(self.delay)
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        reply = self._next()
        time.sleep(self.delay)
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
        self.fetch_requests: list[str] = []
        self.delays: dict[str, float] = {}
        self.reset()

    def reset(self) -> None:
        keys = ("low", "medium", "answer", "weather", "places", "flights", "hotels")
        keys += ("search", "fetch", "artifact")
        self.scripts = {key: [] for key in keys}
        self.scripts["low"].append(AIMessage(TITLE))
        days = [
            itinerary_module.DayDraft(date="", activities=["Tsukiji market", "Senso-ji"], stops=[])
        ] * 5
        self.queues = {
            "Pills": [Pills(items=["Plan a trip", "Check flights"])],
            "ItineraryDraft": [itinerary_module.ItineraryDraft(days=days)],
            "PinNames": [PinNames(items=[])],
            "TripSummary": [TripSummary(trip_type="food", preferences=["street food"])],
        }
        queue_turns(self.queues, classification())
        self.model_calls = []
        self.fetch_requests = []
        self.delays = {}

    @property
    def errors(self) -> type[Exception]:
        return anthropic.APIError

    def model(self, tier: Tier) -> BaseChatModel:
        self.model_calls.append(tier)
        script = self.scripts["low" if tier == "low" else "medium"]
        return FakeChat(script=script, delay=self.delays.get(tier, 0.0))

    def system(self, prompt: str, hint: str) -> SystemMessage:
        return SystemMessage(f"{prompt}\n{hint}")

    def with_tools(
        self, model: BaseChatModel, tools: Sequence[BaseTool], final: bool
    ) -> Runnable[Any, Any]:
        name = tools[0].name
        key = name.split("__")[0] if "__" in name else "answer"
        chat = FakeChat(script=self.scripts[key], delay=getattr(model, "delay", 0.0))
        if key != "fetch":
            return chat

        def record(messages: list[BaseMessage]) -> list[BaseMessage]:
            if not any(isinstance(message, ToolMessage) for message in messages):
                humans = [message for message in messages if isinstance(message, HumanMessage)]
                self.fetch_requests.append(str(humans[-1].content))
            return messages

        return RunnableLambda(record) | chat

    def structured(self, model: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, Any]:
        name = schema.__name__

        def pick(_: Any) -> BaseModel:
            time.sleep(getattr(model, "delay", 0.0))
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


GEO_POINTS: dict[str, tuple[float, float]] = {}


async def geocode_places(places: list[str]) -> str:
    return json.dumps(
        [
            {"name": name, "lat": GEO_POINTS[name][0], "lon": GEO_POINTS[name][1]}
            for name in places
            if name in GEO_POINTS
        ]
    )


SEARCH_HITS = [
    {"title": "Gracery guests", "url": "https://reviews.example/gracery", "snippet": "Great."},
    {"title": "Old blog", "url": "http://insecure.example/gracery", "snippet": "Fine."},
    {"title": "Trip notes", "url": "https://notes.example/tokyo", "snippet": "Nice."},
]
REVIEW_ANSWER = "Guests praise the quiet rooms."
GRACERY_LINKS = [
    {"title": "Gracery guests", "url": "https://reviews.example/gracery"},
    {"title": "Trip notes", "url": "https://notes.example/tokyo"},
]


async def web_search(query: str) -> str:
    return json.dumps(SEARCH_HITS)


fetched: list[str] = []


async def fetch_page(url: str) -> str:
    fetched.append(url)
    return json.dumps({"title": "Gracery guests", "url": url, "text": "Quiet rooms."})


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
            "geo": [data_tool(geocode_places, "geo__geocode")],
            "search": [data_tool(web_search, "search__web")],
            "fetch": [data_tool(fetch_page, "fetch__page")],
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
    return {
        "role": "user",
        "text": text,
        "toolCalls": [],
        "planCard": None,
        "pills": [],
        "sources": [],
        "artifacts": [],
    }


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
        "sources": [],
        "artifacts": [],
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


def first_words_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if "First words" in record.getMessage()]


def test_an_answer_turn_logs_one_first_words_line(
    client: TestClient, user_a: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    script_weather_answer()
    conversation_id = create(client, user_a, "weather in Tokyo")["id"]

    send(client, user_a, conversation_id, "weather in Tokyo")

    records = first_words_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert records[0].name == "src.conversations.router"
    assert records[0].args is not None
    conversation, elapsed_ms = cast("tuple[UUID, int]", records[0].args)
    assert str(conversation) == conversation_id
    assert elapsed_ms >= 0


def test_an_off_topic_turn_logs_one_first_words_line(
    client: TestClient, user_a: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    queue_turns(PROVIDER.queues, classification(verdict="off_topic"))
    conversation_id = create(client, user_a, "tell me a joke")["id"]

    send(client, user_a, conversation_id, "tell me a joke")

    records = first_words_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert records[0].name == "src.conversations.router"
    assert records[0].args is not None
    _, elapsed_ms = cast("tuple[UUID, int]", records[0].args)
    assert elapsed_ms >= 0


def test_the_root_logger_keeps_its_default_level() -> None:
    assert main_module.app is not None
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("src").level == logging.INFO


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


GREETING = "Sure, let me look into that."


def test_a_travel_turn_streams_the_greeting_one_break_then_the_answer(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    script_weather_answer()
    conversation_id = create(client, user_a, "weather in Tokyo")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "weather in Tokyo"))

    assert tokens_of(frames) == [GREETING, "\n\n", ANSWER]
    assert "notice" not in events_of(frames)


def test_a_plan_turn_streams_the_greeting_one_break_then_the_plan_text(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    script_demo()
    conversation_id = create(client, user_a, "3 days in Tokyo from Singapore")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "3 days in Tokyo from Singapore"))

    tokens = tokens_of(frames)
    assert tokens[:2] == [GREETING, "\n\n"]
    assert tokens.count("\n\n") == 1
    assert "".join(tokens[2:]).startswith("Here is your 3-day plan for Tokyo")


def test_a_reopened_greeted_answer_turn_reads_as_its_streamed_tokens_in_one_reply(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    script_weather_answer()
    conversation_id = create(client, user_a, "weather in Tokyo")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "weather in Tokyo"))

    streamed = "".join(tokens_of(frames))
    assert streamed == f"{GREETING}\n\n{ANSWER}"
    detail = detail_of(client, user_a, conversation_id)
    assert detail["messages"] == [
        user_item("weather in Tokyo"),
        assistant_item(streamed, calls=[WEATHER_CALL], pills=PILLS),
    ]


def test_a_reopened_greeted_plan_turn_reads_as_its_streamed_tokens_in_one_reply(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    script_demo()
    conversation_id = create(client, user_a, "3 days in Tokyo from Singapore")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "3 days in Tokyo from Singapore"))

    streamed = "".join(tokens_of(frames))
    assert streamed.startswith(f"{GREETING}\n\n")
    detail = detail_of(client, user_a, conversation_id)
    [user, reply] = detail["messages"]
    assert user == user_item("3 days in Tokyo from Singapore")
    assert reply["text"] == streamed
    assert reply["planCard"] is not None


def test_a_reply_saved_with_a_greeting_builds_as_greeting_break_reply() -> None:
    message = AIMessage("Sunny.", id="m1", additional_kwargs={"greeting": "Happy to help."})

    [item] = build_messages([message], [], [])

    assert item.text == "Happy to help.\n\nSunny."


@pytest.mark.parametrize("saved", [{}, {"greeting": ""}, {"greeting": "  "}])
def test_a_reply_saved_without_a_greeting_builds_as_its_own_text(saved: dict[str, str]) -> None:
    [item] = build_messages([AIMessage("Sunny.", id="m1", additional_kwargs=saved)], [], [])

    assert item.text == "Sunny."


def test_an_off_topic_turn_streams_the_refusal_with_no_greeting_and_no_break(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    queue_turns(PROVIDER.queues, classification(verdict="off_topic"))
    conversation_id = create(client, user_a, "tell me a joke")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "tell me a joke"))

    tokens = tokens_of(frames)
    assert "".join(tokens) == REFUSAL
    assert GREETING not in tokens
    assert "\n\n" not in tokens


def test_a_failed_greeting_adds_no_break_and_no_notice(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["low"].append(RuntimeError("greeting down"))
    script_weather_answer()
    conversation_id = create(client, user_a, "weather in Tokyo")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "weather in Tokyo"))

    assert tokens_of(frames) == [ANSWER]
    assert "notice" not in events_of(frames)


def test_title_is_replaced_after_the_first_turn_only(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["answer"] += [AIMessage("First."), AIMessage("Second.")]
    PROVIDER.scripts["low"] += [AIMessage("Hello."), AIMessage("Sure.")]
    conversation_id = create(client, user_a, "weather in Tokyo please")["id"]

    send(client, user_a, conversation_id, "weather in Tokyo please")
    send(client, user_a, conversation_id, "and tomorrow")

    detail = detail_of(client, user_a, conversation_id)
    assert detail["title"] == TITLE
    assert PROVIDER.model_calls.count("low") == 3
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
    assert set(card) == CARD_KEYS
    assert card["status"] == "open"
    assert [option["id"] for option in card["flights"]] == ["f1", "f2", "f3"]
    assert card["flights"][0] == {
        "id": "f1",
        "summary": "Flight 1",
        "priceUsd": 401.0,
        "deepLink": "https://flight.example/f1",
        "legs": [],
        "location": None,
    }
    assert card["reviewPins"] == []
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
    assert text.startswith("Here is your 3-day plan for Tokyo, and I hope you enjoy it!")
    detail = detail_of(client, user_a, conversation_id)
    calls = [
        {"name": "flights__search", "kind": "retrieve"},
        {"name": "hotels__search", "kind": "retrieve"},
        {"name": "weather__forecast", "kind": "retrieve"},
        {"name": "places__attractions", "kind": "retrieve"},
        {"name": "calculator", "kind": "calculate"},
        {"name": "geo__geocode", "kind": "retrieve"},
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
    PROVIDER.scripts["low"][:] = [AIMessage(GREETING)]
    PROVIDER.scripts["medium"].append(AIMessage("What would you like to change?"))
    conversation_id = create(client, user_a, "trip")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reject", {"type": "reject"}))

    assert tokens_of(frames) == [GREETING, "\n\n", "What would you like to change?"]
    assert events_of(frames)[-2:] == ["pills", "done"]
    detail = detail_of(client, user_a, conversation_id)
    assert [message["text"] for message in detail["messages"]] == [
        "Reject",
        f"{GREETING}\n\nWhat would you like to change?",
    ]


def test_a_budget_change_streams_the_question_about_where_the_money_goes(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_demo()
    conversation_id = create(client, user_a, "3 days in Tokyo from Singapore")["id"]
    send(client, user_a, conversation_id, "3 days in Tokyo from Singapore")
    queue_turns(PROVIDER.queues, classification(route="plan", budget_usd=3000))
    PROVIDER.scripts["medium"].append(AIMessage("Where should the extra money go?"))

    frames = parse_frames(send(client, user_a, conversation_id, "Raise the budget to $3000"))

    assert "".join(tokens_of(frames)) == "Where should the extra money go?"
    assert "plan_card" not in events_of(frames)


def test_an_approve_with_no_plan_gets_the_note_and_no_card(
    client: TestClient, user_a: dict[str, str]
) -> None:
    PROVIDER.scripts["low"][:] = [AIMessage(GREETING)]
    conversation_id = create(client, user_a, "trip")["id"]
    action = {"type": "approve", "cardId": "ghost", "flightId": "f1", "hotelId": "h1"}

    frames = parse_frames(send(client, user_a, conversation_id, "Approve", action))

    assert "plan_card" not in events_of(frames)
    tokens = tokens_of(frames)
    assert tokens[:2] == [GREETING, "\n\n"]
    assert "".join(tokens[2:]).startswith("There is no plan to approve yet.")


def airport(code: str, name: str) -> dict[str, Any]:
    return {"code": code, "name": name, "lat": None, "lon": None}


def test_the_event_and_the_detail_carry_legs_location_stops_and_review_pins(
    client: TestClient, user_a: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    flights = json.loads(FLIGHTS)
    flights[0]["legs"] = [
        {
            "from": airport("SIN", "Changi Airport"),
            "to": airport("HND", "Haneda Airport"),
            "departs": "2026-11-10 08:00",
            "arrives": "2026-11-10 16:00",
        }
    ]
    hotels = json.loads(HOTELS)
    hotels[0]["location"] = {"lat": 35.6, "lon": 139.7}
    monkeypatch.setitem(globals(), "FLIGHTS", json.dumps(flights))
    monkeypatch.setitem(globals(), "HOTELS", json.dumps(hotels))
    monkeypatch.setitem(GEO_POINTS, "Changi Airport", (1.36, 103.99))
    monkeypatch.setitem(GEO_POINTS, "Haneda Airport", (35.55, 139.78))
    monkeypatch.setitem(GEO_POINTS, "Senso-ji, Tokyo", (35.71, 139.79))
    script_demo()
    days = [
        itinerary_module.DayDraft(date="", activities=["Senso-ji"], stops=["senso-ji", "Ghost"])
    ] * 3
    PROVIDER.queues["ItineraryDraft"] = [itinerary_module.ItineraryDraft(days=days)]
    conversation_id = create(client, user_a, "3 days in Tokyo from Singapore")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "3 days in Tokyo from Singapore"))

    card = next(data for event, data in frames if event == "plan_card")
    assert set(card) == CARD_KEYS
    [leg] = card["flights"][0]["legs"]
    assert leg["from"] == {"code": "SIN", "name": "Changi Airport", "lat": 1.36, "lon": 103.99}
    assert leg["to"] == {"code": "HND", "name": "Haneda Airport", "lat": 35.55, "lon": 139.78}
    assert card["flights"][1]["legs"] == []
    assert card["hotels"][0]["location"] == {"lat": 35.6, "lon": 139.7}
    assert card["hotels"][1]["location"] is None
    assert [day["stops"] for day in card["days"]] == [
        [{"name": "Senso-ji", "lat": 35.71, "lon": 139.79}]
    ] * 3
    assert card["reviewPins"] == []
    detail = detail_of(client, user_a, conversation_id)
    assert detail["messages"][1]["planCard"] == card
    used = [call["name"] for call in detail["messages"][1]["toolCalls"]]
    assert "geo__geocode" in used


def sprint_1_card() -> dict[str, Any]:
    return {
        "messageId": "m1",
        "id": "c1",
        "status": "open",
        "flights": [
            {"id": "f1", "summary": "Flight 1", "priceUsd": 1.0, "deepLink": "https://x.example"}
        ],
        "hotels": [],
        "picked": {"flightId": "f1", "hotelId": ""},
        "days": [{"date": "2026-11-10", "activities": ["Walk"], "weather": "mild", "costUsd": 9}],
        "budget": {
            "flightUsd": 1.0,
            "hotelUsd": 0.0,
            "dailyUsd": 9.0,
            "totalUsd": 10.0,
            "budgetUsd": None,
            "overBudget": False,
        },
    }


def test_a_card_saved_in_sprint_1_still_validates_with_empty_map_fields() -> None:
    saved = {key: value for key, value in sprint_1_card().items() if key != "messageId"}

    item = PlanCardItem.model_validate(saved).model_dump(by_alias=True)

    assert item["flights"][0]["legs"] == []
    assert item["flights"][0]["location"] is None
    assert item["days"][0]["stops"] == []
    assert item["reviewPins"] == []


def test_the_detail_of_a_conversation_whose_card_has_no_review_pins_still_builds() -> None:
    card = cast("PlanCard", sprint_1_card())
    assert "reviewPins" not in card

    [item] = build_messages([AIMessage("Here is your plan.", id="m1")], [card], [])

    assert item.plan_card is not None
    assert item.plan_card.review_pins == []
    assert item.plan_card.days[0].stops == []


def test_a_message_saved_before_sources_existed_builds_with_none() -> None:
    [item] = build_messages([AIMessage("Hello.", id="m1")], [], [])

    assert item.sources == []


def test_the_detail_of_a_message_carries_the_sources_saved_on_it() -> None:
    saved = [{"subject": "Hotel Gracery", "links": GRACERY_LINKS}]
    message = AIMessage("Guests like it.", id="m1", additional_kwargs={"sources": saved})

    [item] = build_messages([message], [], [])

    assert item.model_dump(by_alias=True)["sources"] == saved


def script_review_answer(
    *requests: str, fetch_url: str = "https://reviews.example/gracery"
) -> None:
    subjects = requests or ("Hotel Gracery Tokyo reviews",)
    for request in subjects:
        PROVIDER.scripts["answer"].append(call_reply("search_agent", {"request": request}))
        PROVIDER.scripts["search"] += [
            call_reply("search__web", {"query": request}),
            AIMessage("Guests like it."),
        ]
    fetch = {"url": fetch_url}
    PROVIDER.scripts["answer"].append(AIMessage(REVIEW_ANSWER))
    PROVIDER.scripts["fetch"] += [call_reply("fetch__page", fetch), AIMessage("Quiet rooms.")]
    PROVIDER.queues["Classification"] = [Classification(verdict="travel", route="answer")]


def test_a_review_turn_streams_sources_saves_them_and_makes_no_pin_call_without_a_card(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_review_answer()
    PROVIDER.queues["PinNames"] = [
        PinNames(items=[PinName(name="Ichiran", kind="restaurant")]),
        PinNames(items=[]),
    ]
    text = "What do people say about Hotel Gracery?"
    conversation_id = create(client, user_a, text)["id"]

    frames = parse_frames(send(client, user_a, conversation_id, text))

    events = events_of(frames)
    used = [data["name"] for event, data in frames if event == "tool_call"]
    assert {"search__web", "fetch__page"} <= set(used)
    assert "map_pins" not in events
    assert events[-2:] == ["pills", "done"]
    items = [{"subject": "Hotel Gracery Tokyo reviews", "links": GRACERY_LINKS}]
    assert [data for event, data in frames if event == "sources"] == [{"items": items}]
    assert "".join(tokens_of(frames)) == REVIEW_ANSWER
    assert len(PROVIDER.queues["PinNames"]) == 2
    detail = detail_of(client, user_a, conversation_id)
    assert detail["messages"][-1]["sources"] == items
    assert detail["messages"][-1]["text"] == REVIEW_ANSWER


def test_a_review_turn_after_a_plan_pins_found_places_on_the_latest_card(
    client: TestClient, user_a: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(GEO_POINTS, "Ichiran, Tokyo", (35.69, 139.7))
    script_demo()
    conversation_id = create(client, user_a, "3 days in Tokyo from Singapore")["id"]
    first = parse_frames(send(client, user_a, conversation_id, "3 days in Tokyo from Singapore"))
    card = next(data for event, data in first if event == "plan_card")
    script_review_answer()
    PROVIDER.queues["PinNames"] = [
        PinNames(
            items=[
                PinName(name="Ichiran", kind="restaurant"),
                PinName(name="Nowhere Cafe", kind="restaurant"),
            ]
        )
    ]

    frames = parse_frames(send(client, user_a, conversation_id, "Any reviews for Hotel Gracery?"))

    events = events_of(frames)
    assert events.index("sources") < events.index("map_pins")
    pin = {"name": "Ichiran", "kind": "restaurant", "lat": 35.69, "lon": 139.7}
    assert [data for event, data in frames if event == "map_pins"] == [
        {"cardId": card["id"], "pins": [pin]}
    ]
    detail = detail_of(client, user_a, conversation_id)
    assert detail["messages"][1]["planCard"]["reviewPins"] == [pin]
    review = detail["messages"][3]
    items = [{"subject": "Hotel Gracery Tokyo reviews", "links": GRACERY_LINKS}]
    assert review["sources"] == items
    assert {"name": "geo__geocode", "kind": "retrieve"} in review["toolCalls"]


def test_a_hit_url_with_an_ampersand_is_fetched_unescaped_and_listed_first(
    client: TestClient, user_a: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://example.com/r?id=1&lang=en"
    other = {"title": "Other", "url": "https://other.example/x", "snippet": "."}
    page = {"title": "Reviews", "url": url, "snippet": "."}
    monkeypatch.setitem(globals(), "SEARCH_HITS", [other, page])
    fetched.clear()
    script_review_answer(fetch_url=url.replace("&", "&amp;"))
    conversation_id = create(client, user_a, "Reviews please")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reviews please"))

    assert fetched == [url]
    [sourced] = [data for event, data in frames if event == "sources"]
    assert [link["url"] for link in sourced["items"][0]["links"]] == [url, other["url"]]


def test_a_url_that_search_did_not_return_is_refused_and_never_fetched(
    client: TestClient, user_a: dict[str, str]
) -> None:
    fetched.clear()
    script_review_answer(fetch_url="https://evil.example/steer")
    conversation_id = create(client, user_a, "Reviews please")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reviews please"))

    assert fetched == []
    assert "fetch__page" not in [d["name"] for e, d in frames if e == "tool_call"]
    [sourced] = [data for event, data in frames if event == "sources"]
    assert sourced["items"][0]["links"] == GRACERY_LINKS
    detail = detail_of(client, user_a, conversation_id)
    names = [call["name"] for call in detail["messages"][-1]["toolCalls"]]
    assert "fetch__page" not in names
    assert "search__web" in names


def test_the_answer_runs_the_search_agent_at_most_twice_per_turn(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_review_answer("Hotel A Tokyo", "Ramen B Tokyo")
    PROVIDER.scripts["answer"].insert(2, call_reply("search_agent", {"request": "Cafe C Tokyo"}))
    conversation_id = create(client, user_a, "Reviews please")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reviews please"))

    assert "notice" not in events_of(frames)
    [sourced] = [data for event, data in frames if event == "sources"]
    assert [item["subject"] for item in sourced["items"]] == ["Hotel A Tokyo", "Ramen B Tokyo"]
    assert PROVIDER.scripts["search"] == []


def test_a_search_with_no_fetch_makes_the_code_fetch_the_top_three_hits(
    client: TestClient, user_a: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    hits = [
        {"title": f"Hit {n}", "url": f"https://reviews.example/{n}", "snippet": "."}
        for n in range(1, 5)
    ]
    monkeypatch.setitem(globals(), "SEARCH_HITS", hits)
    fetched.clear()
    script_review_answer(fetch_url="https://reviews.example/2")
    conversation_id = create(client, user_a, "Reviews please")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reviews please"))

    assert fetched == ["https://reviews.example/2"]
    used = [data["name"] for event, data in frames if event == "tool_call"]
    assert {"search__web", "fetch__page"} <= set(used)
    assert "".join(tokens_of(frames)) == REVIEW_ANSWER
    [sourced] = [data for event, data in frames if event == "sources"]
    assert sourced["items"][0]["links"][0]["url"] == "https://reviews.example/2"
    [request] = PROVIDER.fetch_requests
    assert "report what reviewers say about Hotel Gracery Tokyo reviews" in request
    positions = [request.index(hit_item["url"]) for hit_item in hits[:3]]
    assert positions == sorted(positions)
    assert hits[3]["url"] not in request


def test_a_model_made_fetch_in_the_search_round_means_no_automatic_fetch(
    client: TestClient, user_a: dict[str, str]
) -> None:
    fetched.clear()
    url = "https://reviews.example/gracery"
    both = AIMessage(
        content="",
        tool_calls=[
            {"name": "search_agent", "args": {"request": "Hotel Gracery"}, "id": "c-search"},
            {"name": "fetch_agent", "args": {"request": url}, "id": "c-fetch"},
        ],
    )
    PROVIDER.scripts["answer"] += [both, AIMessage(REVIEW_ANSWER)]
    PROVIDER.scripts["search"] += [call_reply("search__web", {"query": "g"}), AIMessage("Ok.")]
    PROVIDER.scripts["fetch"] += [call_reply("fetch__page", {"url": url}), AIMessage("Quiet.")]
    PROVIDER.queues["Classification"] = [Classification(verdict="travel", route="answer")]
    conversation_id = create(client, user_a, "Reviews please")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reviews please"))

    assert fetched == [url]
    assert PROVIDER.scripts["fetch"] == []
    assert "".join(tokens_of(frames)) == REVIEW_ANSWER


def test_a_search_without_hits_makes_no_automatic_fetch(
    client: TestClient, user_a: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(globals(), "SEARCH_HITS", [])
    fetched.clear()
    script_review_answer()
    conversation_id = create(client, user_a, "Reviews please")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reviews please"))

    assert fetched == []
    assert len(PROVIDER.scripts["fetch"]) == 2
    assert "notice" not in events_of(frames)
    assert "".join(tokens_of(frames)) == REVIEW_ANSWER


def test_without_a_fetch_agent_a_search_is_answered_with_no_fetch_attempt(
    client: TestClient, user_a: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(app.state.planner.agents, "fetch")
    fetched.clear()
    script_review_answer()
    PROVIDER.scripts["fetch"].clear()
    conversation_id = create(client, user_a, "Reviews please")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reviews please"))

    assert fetched == []
    assert PROVIDER.fetch_requests == []
    assert "notice" not in events_of(frames)
    used = [data["name"] for event, data in frames if event == "tool_call"]
    assert "search__web" in used
    assert "fetch__page" not in used
    assert "".join(tokens_of(frames)) == REVIEW_ANSWER


def test_a_third_model_made_fetch_is_refused_and_no_automatic_fetch_fires(
    client: TestClient, user_a: dict[str, str]
) -> None:
    fetched.clear()
    url = "https://reviews.example/gracery"
    first_round = AIMessage(
        content="",
        tool_calls=[
            {"name": "search_agent", "args": {"request": "Hotel Gracery"}, "id": "c-search"},
            {"name": "fetch_agent", "args": {"request": url}, "id": "c-fetch-1"},
        ],
    )
    PROVIDER.scripts["answer"] += [
        first_round,
        call_reply("fetch_agent", {"request": url}),
        call_reply("fetch_agent", {"request": url}),
        AIMessage(REVIEW_ANSWER),
    ]
    PROVIDER.scripts["search"] += [call_reply("search__web", {"query": "g"}), AIMessage("Ok.")]
    for _ in range(2):
        PROVIDER.scripts["fetch"] += [call_reply("fetch__page", {"url": url}), AIMessage("Quiet.")]
    PROVIDER.queues["Classification"] = [Classification(verdict="travel", route="answer")]
    conversation_id = create(client, user_a, "Reviews please")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Reviews please"))

    assert fetched == [url, url]
    assert len(PROVIDER.fetch_requests) == 2
    assert PROVIDER.scripts["fetch"] == []
    assert PROVIDER.scripts["answer"] == []
    assert "notice" not in events_of(frames)
    assert "".join(tokens_of(frames)) == REVIEW_ANSWER


EXPORT_ANSWER = "Your file is ready."
CSV_HEADER = "Section,Date,Item,Details,Weather,Cost in USD"


def script_export(fmt: str) -> None:
    PROVIDER.queues["Classification"] = [Classification(verdict="travel", route="answer")]
    PROVIDER.scripts["answer"] += [
        call_reply("artifact_agent", {"request": fmt}),
        AIMessage(EXPORT_ANSWER),
    ]
    PROVIDER.scripts["artifact"] += [
        call_reply("artifact__export", {"format": fmt}),
        AIMessage("Done."),
    ]


def plan_and_export(
    client: TestClient, headers: dict[str, str], fmt: str = "csv"
) -> tuple[str, list[tuple[str, Any]]]:
    script_demo()
    conversation_id = create(client, headers, "3 days in Tokyo from Singapore")["id"]
    send(client, headers, conversation_id, "3 days in Tokyo from Singapore")
    script_export(fmt)
    frames = parse_frames(send(client, headers, conversation_id, "Send me the plan as a file"))
    return conversation_id, frames


def artifact_of(frames: list[tuple[str, Any]]) -> dict[str, Any]:
    [artifact] = [data for event, data in frames if event == "artifact"]
    return cast("dict[str, Any]", artifact)


def download(
    client: TestClient, headers: dict[str, str], conversation_id: str, artifact: dict[str, Any]
) -> Any:
    return client.get(f"/conversations/{conversation_id}/exports/{artifact['id']}", headers=headers)


def test_an_export_turn_streams_the_artifact_and_the_detail_carries_it(
    client: TestClient, user_a: dict[str, str]
) -> None:
    conversation_id, frames = plan_and_export(client, user_a)

    artifact = artifact_of(frames)
    assert set(artifact) == {"id", "format", "fileName", "url", "draft"}
    assert artifact["format"] == "csv"
    assert artifact["fileName"] == "tokyo-plan-draft.csv"
    assert artifact["draft"] is True
    assert artifact["url"] == f"/api/conversations/{conversation_id}/exports/{artifact['id']}"
    assert events_of(frames)[-2:] == ["pills", "done"]
    assert "".join(tokens_of(frames)) == EXPORT_ANSWER
    assert artifact["url"] not in "".join(tokens_of(frames))
    detail = detail_of(client, user_a, conversation_id)
    assert detail["messages"][-1]["artifacts"] == [artifact]
    assert detail["messages"][1]["artifacts"] == []
    assert {"name": "artifact__export", "kind": "retrieve"} in detail["messages"][-1]["toolCalls"]


def test_the_owner_downloads_the_file_with_the_safe_headers(
    client: TestClient, user_a: dict[str, str]
) -> None:
    conversation_id, frames = plan_and_export(client, user_a)
    artifact = artifact_of(frames)

    response = download(client, user_a, conversation_id, artifact)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="tokyo-plan-draft.csv"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    body = response.content.decode("utf-8-sig")
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert body.splitlines()[0] == CSV_HEADER
    assert "Draft — not approved" in body


def test_another_users_export_is_a_404(
    client: TestClient, user_a: dict[str, str], user_b: dict[str, str]
) -> None:
    conversation_id, frames = plan_and_export(client, user_a)
    artifact = artifact_of(frames)

    response = download(client, user_b, conversation_id, artifact)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "attachment" not in response.headers.get("content-disposition", "")


def test_an_unknown_malformed_or_foreign_export_is_a_404(
    client: TestClient, user_a: dict[str, str]
) -> None:
    conversation_id, frames = plan_and_export(client, user_a)
    artifact = artifact_of(frames)
    other_id = create(client, user_a, "another chat")["id"]

    for path in (
        f"/conversations/{conversation_id}/exports/{uuid4()}",
        f"/conversations/{conversation_id}/exports/garbage",
        f"/conversations/{other_id}/exports/{artifact['id']}",
        f"/conversations/{uuid4()}/exports/{artifact['id']}",
    ):
        response = client.get(path, headers=user_a)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")


def test_an_export_without_a_plan_card_makes_no_file(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_export("csv")
    conversation_id = create(client, user_a, "Send me the plan")["id"]

    frames = parse_frames(send(client, user_a, conversation_id, "Send me the plan"))

    assert "artifact" not in events_of(frames)
    detail = detail_of(client, user_a, conversation_id)
    assert detail["messages"][-1]["artifacts"] == []


def test_an_approved_plan_exports_a_final_file(client: TestClient, user_a: dict[str, str]) -> None:
    script_demo()
    conversation_id = create(client, user_a, "3 days in Tokyo from Singapore")["id"]
    first = parse_frames(send(client, user_a, conversation_id, "3 days in Tokyo from Singapore"))
    card = next(data for event, data in first if event == "plan_card")
    action = {"type": "approve", "cardId": card["id"], "flightId": "f2", "hotelId": "h3"}
    send(client, user_a, conversation_id, "Approve", action)
    script_export("excel")

    frames = parse_frames(send(client, user_a, conversation_id, "Export it to excel"))

    artifact = artifact_of(frames)
    assert artifact["format"] == "xlsx"
    assert artifact["fileName"] == "tokyo-plan.xlsx"
    assert artifact["draft"] is False
    response = download(client, user_a, conversation_id, artifact)
    assert response.status_code == 200
    assert response.content.startswith(b"PK")


def test_a_message_saved_before_exports_existed_builds_with_none() -> None:
    [item] = build_messages([AIMessage("Hello.", id="m1")], [], [])

    assert item.artifacts == []


def test_the_detail_of_a_message_carries_the_artifacts_saved_on_it() -> None:
    saved = [
        {
            "id": str(uuid4()),
            "format": "docx",
            "fileName": "tokyo-plan.docx",
            "url": "/api/conversations/c/exports/e",
            "draft": False,
        }
    ]
    message = AIMessage("Ready.", id="m1", additional_kwargs={"artifacts": saved})

    [item] = build_messages([message], [], [])

    assert item.model_dump(by_alias=True)["artifacts"] == saved


TIER_DELAYS = {"low": 0.05, "medium": 0.2, "high": 0.5}
TRIP_TEXT = "3 days in Tokyo from Singapore"
MESSAGES = {
    "travel": "weather in Tokyo",
    "plan": TRIP_TEXT,
    "off_topic": "tell me a joke",
}


def script_kind(kind: str, greeted: bool) -> None:
    PROVIDER.reset()
    if greeted:
        PROVIDER.scripts["low"].append(AIMessage(GREETING))
    if kind == "off_topic":
        queue_turns(PROVIDER.queues, classification(verdict="off_topic"))
    elif kind == "plan":
        script_demo()
    else:
        script_weather_answer()
        queue_turns(PROVIDER.queues, classification(verdict="travel"))


def run_turn(
    client: TestClient, headers: dict[str, str], text: str
) -> tuple[str, list[tuple[str, Any]]]:
    conversation_id = create(client, headers, text)["id"]
    return conversation_id, parse_frames(send(client, headers, conversation_id, text))


def first_words_ms(caplog: pytest.LogCaptureFixture) -> int:
    [record] = first_words_records(caplog)
    assert record.args is not None
    _, elapsed_ms = cast("tuple[UUID, int]", record.args)
    return elapsed_ms


def timed_first_words(
    client: TestClient, headers: dict[str, str], caplog: pytest.LogCaptureFixture, text: str
) -> int:
    conversation_id = create(client, headers, text)["id"]
    caplog.clear()
    send(client, headers, conversation_id, text)
    return first_words_ms(caplog)


def graph_with_classify_tier(tier: Tier) -> Any:
    planner = app.state.planner
    settings = planner.settings.model_copy(update={"classify_tier": tier})
    return build_graph(app.state.graph.checkpointer, replace(planner, settings=settings))


def test_e2e_3_approving_a_plan_opens_with_a_greeting(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_demo()
    conversation_id, first = run_turn(client, user_a, TRIP_TEXT)
    card = next(data for event, data in first if event == "plan_card")
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    action = {"type": "approve", "cardId": card["id"], "flightId": "f2", "hotelId": "h3"}

    frames = parse_frames(send(client, user_a, conversation_id, "Approve", action))

    tokens = tokens_of(frames)
    assert tokens[:2] == [GREETING, "\n\n"]
    assert "https://flight.example/f2" in "".join(tokens[2:])
    assert "notice" not in events_of(frames)


def test_e2e_4_rejecting_a_plan_opens_with_a_greeting(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_demo()
    conversation_id, _ = run_turn(client, user_a, TRIP_TEXT)
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    PROVIDER.scripts["medium"].append(AIMessage("What would you like to change?"))

    frames = parse_frames(send(client, user_a, conversation_id, "Reject", {"type": "reject"}))

    assert tokens_of(frames) == [GREETING, "\n\n", "What would you like to change?"]
    assert "notice" not in events_of(frames)


@pytest.mark.parametrize("kind", ["travel", "off_topic"])
def test_e2e_6_classify_asks_the_medium_tier_model_by_default(
    client: TestClient, user_a: dict[str, str], kind: str
) -> None:
    script_kind(kind, greeted=False)

    run_turn(client, user_a, MESSAGES[kind])

    assert get_settings().classify_tier == "medium"
    assert PROVIDER.model_calls[0] == "medium"
    assert "high" not in PROVIDER.model_calls


@pytest.mark.parametrize("kind", ["travel", "plan", "off_topic"])
def test_e2e_7_first_words_arrive_sooner_than_today(
    client: TestClient,
    user_a: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    text = MESSAGES[kind]
    with monkeypatch.context() as today_path:
        today_path.setattr(app.state, "graph", graph_with_classify_tier("high"))
        script_kind(kind, greeted=False)
        PROVIDER.delays.update(TIER_DELAYS)
        before = timed_first_words(client, user_a, caplog, text)
    script_kind(kind, greeted=True)
    PROVIDER.delays.update(TIER_DELAYS)

    after = timed_first_words(client, user_a, caplog, text)

    assert after < before


def test_e2e_8_a_reopened_conversation_keeps_each_greeting_in_its_reply(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_demo()
    PROVIDER.scripts["low"].append(AIMessage("Happy to plan that."))
    conversation_id, plan_frames = run_turn(client, user_a, TRIP_TEXT)
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    script_weather_answer()
    queue_turns(PROVIDER.queues, classification())
    answer_frames = parse_frames(send(client, user_a, conversation_id, "weather in Tokyo"))

    streamed_plan = "".join(tokens_of(plan_frames))
    streamed_answer = "".join(tokens_of(answer_frames))
    assert streamed_plan.startswith("Happy to plan that.\n\n")
    assert streamed_answer == f"{GREETING}\n\n{ANSWER}"
    detail = detail_of(client, user_a, conversation_id)
    texts = [message["text"] for message in detail["messages"]]
    assert texts == [TRIP_TEXT, streamed_plan, "weather in Tokyo", streamed_answer]


def test_e2e_9_each_kind_of_message_lands_on_the_same_path_as_today(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_kind("off_topic", greeted=False)
    _, refused = run_turn(client, user_a, MESSAGES["off_topic"])
    script_kind("travel", greeted=False)
    _, answered = run_turn(client, user_a, MESSAGES["travel"])
    script_kind("plan", greeted=False)
    _, planned = run_turn(client, user_a, MESSAGES["plan"])

    assert "".join(tokens_of(refused)) == REFUSAL
    assert "plan_card" not in events_of(refused)
    assert tokens_of(answered) == [ANSWER]
    assert "plan_card" not in events_of(answered)
    assert "tool_call" in events_of(answered)
    assert "plan_card" in events_of(planned)


def test_e2e_11_the_greeting_adds_one_low_tier_call_and_an_off_topic_turn_adds_none(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_weather_answer()
    conversation_id, _ = run_turn(client, user_a, "weather in Tokyo")
    PROVIDER.model_calls.clear()
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    script_weather_answer()

    send(client, user_a, conversation_id, "and tomorrow")

    assert PROVIDER.model_calls.count("low") == 1
    assert PROVIDER.scripts["low"] == []
    PROVIDER.model_calls.clear()
    PROVIDER.scripts["low"].append(AIMessage(GREETING))
    queue_turns(PROVIDER.queues, classification(verdict="off_topic"))

    send(client, user_a, conversation_id, "tell me a joke")

    assert PROVIDER.model_calls.count("low") == 0
    assert len(PROVIDER.scripts["low"]) == 1


def test_e2e_12_no_words_appear_before_classify_has_finished(
    client: TestClient, user_a: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    script_kind("travel", greeted=True)
    PROVIDER.delays["medium"] = 0.3
    conversation_id = create(client, user_a, MESSAGES["travel"])["id"]
    caplog.clear()

    frames = parse_frames(send(client, user_a, conversation_id, MESSAGES["travel"]))

    assert tokens_of(frames) == [GREETING, "\n\n", ANSWER]
    elapsed_ms = first_words_ms(caplog)
    assert elapsed_ms >= 290
    assert elapsed_ms < 800


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("travel", {"low": 2, "medium": 3}),
        ("plan", {"low": 3, "medium": 4}),
    ],
)
def test_e2e_13_every_other_step_keeps_its_model_tier(
    client: TestClient, user_a: dict[str, str], kind: str, expected: dict[str, int]
) -> None:
    script_kind(kind, greeted=True)

    run_turn(client, user_a, MESSAGES[kind])

    settings = get_settings()
    assert (settings.answer_tier, settings.title_tier, settings.helper_tier) == (
        "medium",
        "low",
        "low",
    )
    assert Counter(PROVIDER.model_calls) == expected


def test_e2e_14_the_greeting_then_the_answer_then_the_pills_arrive_in_order(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_kind("travel", greeted=True)

    _, frames = run_turn(client, user_a, MESSAGES["travel"])

    events = events_of(frames)
    token_positions = [index for index, event in enumerate(events) if event == "token"]
    assert tokens_of(frames) == [GREETING, "\n\n", ANSWER]
    assert token_positions[0] < events.index("tool_call")
    assert token_positions[-1] < events.index("pills")
    assert events[-2:] == ["pills", "done"]
    assert frames[-2][1] == {"items": PILLS}


SUMMARY_BUDGET = 3


def script_dated_plan() -> tuple[str, str]:
    script_demo()
    start = (today() + timedelta(days=30)).isoformat()
    end = (today() + timedelta(days=33)).isoformat()
    queue_turns(
        PROVIDER.queues,
        classification(
            route="plan",
            verdict="travel",
            destination="Tokyo",
            origin="Singapore",
            start_date=start,
            end_date=end,
            interests=["food"],
        ),
    )
    PROVIDER.queues["TripSummary"] = [
        TripSummary(trip_type="food", preferences=["street food"])
    ] * SUMMARY_BUDGET
    return start, end


def summary_calls() -> int:
    return SUMMARY_BUDGET - len(PROVIDER.queues["TripSummary"])


def stored_summaries(client: TestClient, headers: dict[str, str]) -> list[Any]:
    assert client.portal is not None
    pool = app.state.pool
    uid = UUID(headers[USER_HEADER])
    return list(client.portal.call(starters_repository.newest_summaries, pool, uid))


def switch_remembering(client: TestClient, headers: dict[str, str], enabled: bool) -> None:
    assert client.portal is not None
    pool = app.state.pool
    uid = UUID(headers[USER_HEADER])
    client.portal.call(starters_repository.set_enabled, pool, uid, enabled)


def test_a_plan_turn_stores_one_summary_for_its_owner_with_destination_and_dates(
    client: TestClient, user_a: dict[str, str], user_b: dict[str, str]
) -> None:
    start, end = script_dated_plan()
    conversation_id, _ = run_turn(client, user_a, MESSAGES["plan"])

    [row] = stored_summaries(client, user_a)

    assert str(row.conversation_id) == conversation_id
    assert row.destination == "Tokyo"
    assert (row.start_date.isoformat(), row.end_date.isoformat()) == (start, end)
    assert row.trip_type == "food"
    assert row.preferences == ["street food"]
    assert stored_summaries(client, user_b) == []
    assert summary_calls() == 1


def test_no_summary_frame_is_ever_sent(client: TestClient, user_a: dict[str, str]) -> None:
    script_dated_plan()

    _, frames = run_turn(client, user_a, MESSAGES["plan"])

    assert "summary" not in events_of(frames)
    assert "notice" not in events_of(frames)
    assert events_of(frames)[-2:] == ["pills", "done"]


def test_with_remembering_off_there_is_no_summary_call_and_no_row(
    client: TestClient, user_a: dict[str, str]
) -> None:
    switch_remembering(client, user_a, False)
    script_dated_plan()

    run_turn(client, user_a, MESSAGES["plan"])

    assert summary_calls() == 0
    assert stored_summaries(client, user_a) == []


def test_a_second_turn_with_the_same_details_makes_no_second_summary_call(
    client: TestClient, user_a: dict[str, str]
) -> None:
    script_dated_plan()
    conversation_id, _ = run_turn(client, user_a, MESSAGES["plan"])
    assert summary_calls() == 1
    script_weather_answer()
    queue_turns(PROVIDER.queues, classification())

    send(client, user_a, conversation_id, "and what about the weather?")

    assert summary_calls() == 1
    assert len(stored_summaries(client, user_a)) == 1


def test_when_saving_the_summary_fails_the_turn_still_ends_with_pills_then_done(
    client: TestClient, user_a: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_upsert(*args: Any, **fields: Any) -> bool:
        raise ValueError("bad date")

    monkeypatch.setattr(starters_repository, "upsert_summary", failing_upsert)
    script_dated_plan()

    _, frames = run_turn(client, user_a, MESSAGES["plan"])

    events = events_of(frames)
    assert events[-2:] == ["pills", "done"]
    assert "notice" not in events
    assert stored_summaries(client, user_a) == []


STARTERS_PATH = "/conversations/starters"
MADE_UP_PHRASE = "zorblax-quux-7731"


def next_march() -> tuple[str, str]:
    now = today()
    year = now.year if now.month < 3 else now.year + 1
    return date(year, 3, 10).isoformat(), date(year, 3, 14).isoformat()


def script_trip(
    destination: str,
    *,
    dates: tuple[str, str] | None = None,
    trip_type: str = "food",
    preferences: list[str] | None = None,
) -> None:
    script_demo()
    fields: dict[str, Any] = {"length_days": 3}
    if dates is not None:
        fields = {"start_date": dates[0], "end_date": dates[1]}
    queue_turns(
        PROVIDER.queues,
        classification(
            route="plan",
            verdict="travel",
            destination=destination,
            origin="Singapore",
            interests=["food"],
            **fields,
        ),
    )
    PROVIDER.queues["TripSummary"] = [
        TripSummary(trip_type=trip_type, preferences=preferences or [])
    ]


def discuss_trip(
    client: TestClient,
    headers: dict[str, str],
    destination: str,
    *,
    text: str | None = None,
    dates: tuple[str, str] | None = None,
    trip_type: str = "food",
    preferences: list[str] | None = None,
) -> str:
    PROVIDER.reset()
    script_trip(destination, dates=dates, trip_type=trip_type, preferences=preferences)
    conversation_id, _ = run_turn(client, headers, text or f"3 days in {destination}")
    return conversation_id


def starters_of(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get(STARTERS_PATH, headers=headers)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def set_starters(client: TestClient, headers: dict[str, str], enabled: bool) -> None:
    response = client.put(f"{STARTERS_PATH}/setting", json={"enabled": enabled}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"enabled": enabled}


def clear_starters(client: TestClient, headers: dict[str, str]) -> None:
    assert client.delete(f"{STARTERS_PATH}/history", headers=headers).status_code == 204


def test_gs_e2e_1_a_past_trip_shows_up_as_one_to_four_starters_about_it(
    client: TestClient, user_a: dict[str, str]
) -> None:
    discuss_trip(client, user_a, "Da Nang", dates=next_march())

    body = starters_of(client, user_a)

    assert body["enabled"] is True
    assert 1 <= len(body["items"]) <= 4
    assert all("Da Nang" in item for item in body["items"])
    assert "March" in body["items"][0]


def test_gs_e2e_3_the_setting_is_on_for_a_user_who_never_touched_it(
    client: TestClient, user_a: dict[str, str]
) -> None:
    assert starters_of(client, user_a) == {"enabled": True, "items": []}


def test_gs_e2e_5_clearing_the_history_removes_starters_and_keeps_every_conversation(
    client: TestClient, user_a: dict[str, str]
) -> None:
    ids = [discuss_trip(client, user_a, city) for city in ("Da Nang", "Hanoi", "Hue")]
    assert starters_of(client, user_a)["items"] != []
    before = [detail_of(client, user_a, conversation_id) for conversation_id in ids]

    clear_starters(client, user_a)

    assert starters_of(client, user_a) == {"enabled": True, "items": []}
    after = [detail_of(client, user_a, conversation_id) for conversation_id in ids]
    assert after == before
    assert all(len(detail["messages"]) == 2 for detail in after)
    listed = client.get("/conversations", headers=user_a).json()["items"]
    assert {item["id"] for item in listed} == set(ids)


def test_gs_e2e_6_a_new_trip_after_a_clear_feeds_new_starters(
    client: TestClient, user_a: dict[str, str]
) -> None:
    discuss_trip(client, user_a, "Da Nang")
    clear_starters(client, user_a)

    discuss_trip(client, user_a, "Hanoi")

    items = starters_of(client, user_a)["items"]
    assert items != []
    assert all("Hanoi" in item for item in items)
    assert not any("Da Nang" in item for item in items)


def test_gs_e2e_7_the_setting_survives_a_new_sign_in(
    client: TestClient, user_a: dict[str, str]
) -> None:
    discuss_trip(client, user_a, "Da Nang")

    set_starters(client, user_a, False)

    for _ in range(2):
        assert starters_of(client, user_a) == {"enabled": False, "items": []}
    assert client.portal is not None
    uid = UUID(user_a[USER_HEADER])
    assert client.portal.call(starters_repository.get_enabled, app.state.pool, uid) is False


def test_gs_e2e_8_follow_up_pills_behave_as_today_with_past_summaries(
    client: TestClient, user_a: dict[str, str]
) -> None:
    discuss_trip(client, user_a, "Da Nang")
    assert starters_of(client, user_a)["enabled"] is True
    script_kind("travel", greeted=False)

    conversation_id, frames = run_turn(client, user_a, MESSAGES["travel"])

    assert events_of(frames)[-2:] == ["pills", "done"]
    assert "summary" not in events_of(frames)
    items = frames[-2][1]["items"]
    assert 2 <= len(items) <= 4
    assert items == PILLS
    assert detail_of(client, user_a, conversation_id)["messages"][-1]["pills"] == PILLS


def test_gs_e2e_9_another_users_trips_never_reach_a_user_with_none(
    client: TestClient, user_a: dict[str, str], user_b: dict[str, str]
) -> None:
    discuss_trip(client, user_a, "Da Nang")
    assert starters_of(client, user_a)["items"] != []

    body = starters_of(client, user_b)

    assert body == {"enabled": True, "items": []}
    assert "Da Nang" not in json.dumps(body)


async def summary_columns(pool: Any) -> set[str]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'trip_summaries'"
        )
        return {row["column_name"] for row in await cursor.fetchall()}


def test_gs_e2e_10_the_kept_summary_holds_only_trip_fields(
    client: TestClient, user_a: dict[str, str]
) -> None:
    text = f"5 days in Da Nang, I adore {MADE_UP_PHRASE} noodles"
    conversation_id = discuss_trip(
        client, user_a, "Da Nang", text=text, preferences=["street food"]
    )
    detail = detail_of(client, user_a, conversation_id)
    assert MADE_UP_PHRASE in detail["messages"][0]["text"]

    [row] = stored_summaries(client, user_a)

    assert row.destination == "Da Nang"
    assert row.trip_type == "food"
    assert row.preferences == ["street food"]
    assert MADE_UP_PHRASE not in repr(row)
    assert MADE_UP_PHRASE not in json.dumps(starters_of(client, user_a))
    assert client.portal is not None
    columns = client.portal.call(summary_columns, app.state.pool)
    assert columns == {
        "conversation_id",
        "user_id",
        "destination",
        "start_date",
        "end_date",
        "trip_type",
        "preferences",
        "updated_at",
    }


def test_gs_e2e_11_opening_new_chat_makes_no_model_call(
    client: TestClient, user_a: dict[str, str]
) -> None:
    discuss_trip(client, user_a, "Da Nang")
    PROVIDER.model_calls.clear()

    for _ in range(3):
        assert starters_of(client, user_a)["items"] != []

    assert PROVIDER.model_calls == []


def test_gs_e2e_13_with_the_setting_off_new_trips_are_not_kept_but_old_ones_stay(
    client: TestClient, user_a: dict[str, str]
) -> None:
    discuss_trip(client, user_a, "Da Nang")
    set_starters(client, user_a, False)

    discuss_trip(client, user_a, "Hanoi")
    assert [row.destination for row in stored_summaries(client, user_a)] == ["Da Nang"]
    set_starters(client, user_a, True)

    items = starters_of(client, user_a)["items"]
    assert items != []
    assert all("Da Nang" in item for item in items)
    assert not any("Hanoi" in item for item in items)


def test_gs_e2e_14_the_summary_follows_a_changed_destination(
    client: TestClient, user_a: dict[str, str]
) -> None:
    conversation_id = discuss_trip(client, user_a, "Paris")
    assert any("Paris" in item for item in starters_of(client, user_a)["items"])
    PROVIDER.reset()
    script_trip("Rome")

    send(client, user_a, conversation_id, "Change the destination to Rome")

    [row] = stored_summaries(client, user_a)
    assert row.destination == "Rome"
    items = starters_of(client, user_a)["items"]
    assert items != []
    assert all("Rome" in item for item in items)
    assert not any("Paris" in item for item in items)


def test_gs_e2e_15_only_the_five_newest_summarized_conversations_count(
    client: TestClient, user_a: dict[str, str]
) -> None:
    cities = ["Lima", "Cairo", "Hue", "Cairo", "Hue", "Cairo"]
    for city in cities:
        discuss_trip(client, user_a, city)

    items = starters_of(client, user_a)["items"]

    assert 1 <= len(items) <= 4
    assert not any("Lima" in item for item in items)
    assert any("Cairo" in item for item in items)
    assert any("Hue" in item for item in items)
