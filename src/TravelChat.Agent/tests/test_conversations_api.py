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
from src.graph.classify import Classification
from src.graph.deps import PlannerDeps
from src.graph.state import PlanCard
from src.graph.suggest import Pills
from src.main import app
from src.settings import Settings, Tier, get_settings
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
        self.fetch_requests: list[str] = []
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
        }
        queue_turns(self.queues, classification())
        self.model_calls = []
        self.fetch_requests = []

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
        chat = FakeChat(script=self.scripts[key])
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
    conversation_id = create(client, user_a, "trip")["id"]
    action = {"type": "approve", "cardId": "ghost", "flightId": "f1", "hotelId": "h1"}

    frames = parse_frames(send(client, user_a, conversation_id, "Approve", action))

    assert "plan_card" not in events_of(frames)
    assert "".join(tokens_of(frames)).startswith("There is no plan to approve yet.")


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
