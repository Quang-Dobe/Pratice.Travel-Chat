import json
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from datetime import timedelta
from typing import Any, Literal, cast
from uuid import uuid4

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from src.agents.registry import build_agents
from src.graph.builder import ChatGraph, build_graph, thread_config
from src.graph.classify import Classification
from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.graph.suggest import Pills
from src.settings import Settings, Tier
from src.utils.modules import load
from src.utils.tools import Tools, calculator, time_tool, with_events
from src.utils.trip import NO_FORECAST, today

type Event = dict[str, Any]

DAYS = 5
NOTE_OLD = "That Approve belongs to an older plan, so nothing was approved."
NOTE_NO_CARD = "There is no plan to approve yet."
NOTE_WRONG_PICK = "The picked flight or hotel is not on the current plan"
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


def listing(prefix: str, label: str, prices: Sequence[float]) -> str:
    return json.dumps(
        [
            {
                "id": f"{prefix}{index}",
                "summary": f"{label} {index}",
                "priceUsd": price,
                "deepLink": f"https://{label.lower()}.example/{prefix}{index}",
            }
            for index, price in enumerate(prices, start=1)
        ]
    )


FLIGHTS = listing("f", "Flight", [450.0, 520.0, 610.0, 700.0])
HOTELS = listing("h", "Hotel", [600.0, 700.0, 800.0, 900.0])
CHEAPEST = {
    "id": "h5",
    "summary": "Hotel 5",
    "priceUsd": 550.0,
    "deepLink": "https://hotel.example/h5",
}
CHEAPER_HOTELS = json.dumps([CHEAPEST, *json.loads(HOTELS)[:2]])
ATTRACTIONS = json.dumps([{"name": "Senso-ji", "category": "temple"}])


class FakeChat(BaseChatModel):
    script: Any = None
    seen: Any = None

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.seen.append(list(messages))
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
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        reply = self._next(messages)
        chunks = [
            tool_call_chunk(
                name=call["name"], args=json.dumps(call["args"]), id=call["id"], index=i
            )
            for i, call in enumerate(reply.tool_calls)
        ]
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=reply.content, tool_call_chunks=chunks)
        )


class FakeProvider:
    def __init__(self) -> None:
        keys = ("low", "medium", "answer", "weather", "places", "flights", "hotels")
        self.scripts: dict[str, list[Any]] = {key: [] for key in keys}
        self.queues: dict[str, list[Any]] = {}
        self.seen: list[list[BaseMessage]] = []
        self.binds: list[tuple[str, bool]] = []
        self.structured_calls: list[str] = []

    @property
    def errors(self) -> type[Exception]:
        return RuntimeError

    def model(self, tier: Tier) -> BaseChatModel:
        key = "low" if tier == "low" else "medium"
        return FakeChat(script=self.scripts[key], seen=self.seen)

    def system(self, prompt: str, hint: str) -> SystemMessage:
        return SystemMessage(f"{prompt}\n{hint}")

    def with_tools(
        self, model: BaseChatModel, tools: Sequence[BaseTool], final: bool
    ) -> Runnable[Any, Any]:
        name = tools[0].name
        key = name.split("__")[0] if "__" in name else "answer"
        self.binds.append((key, final))
        return FakeChat(script=self.scripts[key], seen=self.seen)

    def structured(self, model: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, Any]:
        name = schema.__name__

        def pick(_: Any) -> BaseModel:
            self.structured_calls.append(name)
            queue = self.queues[name]
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return cast("BaseModel", item)

        return RunnableLambda(pick)


def classification(
    *,
    route: Literal["answer", "plan"] = "plan",
    verdict: Literal["travel", "weather", "off_topic"] = "travel",
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


def call_reply(name: str, args: dict[str, Any]) -> AIMessage:
    call = {"name": name, "args": args, "id": f"call-{uuid4().hex[:8]}"}
    return AIMessage(content="", tool_calls=[call])


def of_kind(events: list[Event], name: str) -> list[Event]:
    return [event for event in events if event["event"] == name]


class Scenario:
    def __init__(self) -> None:
        self.provider = FakeProvider()
        self.calls: list[str] = []
        self.hotel_results = [HOTELS]
        self.hotel_error: Exception | None = None
        settings = Settings(database_url="postgresql://unused", jwks_url="http://unused")
        tools = Tools(
            local=[with_events(calculator), with_events(time_tool)],
            domains={
                "flights": [self._tool("flights__search", self._flights)],
                "hotels": [self._tool("hotels__search", self._hotels)],
                "weather": [self._tool("weather__forecast", self._forecast)],
                "places": [self._tool("places__attractions", self._attractions)],
            },
        )
        deps = PlannerDeps(
            provider=self.provider,
            settings=settings,
            tools=tools,
            agents=build_agents(self.provider, tools, settings),
        )
        self.graph: ChatGraph = build_graph(InMemorySaver(), deps)
        self.config = thread_config(uuid4(), uuid4())
        self.provider.scripts["low"].append(AIMessage("Tokyo trip"))
        self.provider.queues["Pills"] = [Pills(items=["Cheaper hotel", "Add places"])]
        days = [
            itinerary_module.DayDraft(date="", activities=["Tsukiji market", "Hike Mt. Takao"])
        ] * DAYS
        self.provider.queues["ItineraryDraft"] = [itinerary_module.ItineraryDraft(days=days)]

    def _tool(self, name: str, function: Callable[..., Any]) -> BaseTool:
        return with_events(
            StructuredTool.from_function(coroutine=function, name=name, description=name)
        )

    async def _flights(
        self, origin: str, destination: str, departDate: str, returnDate: str, travellers: int = 1
    ) -> str:
        self.calls.append("flights__search")
        return FLIGHTS

    async def _hotels(self, city: str, checkIn: str, checkOut: str, travellers: int = 1) -> str:
        self.calls.append("hotels__search")
        if self.hotel_error is not None:
            raise self.hotel_error
        return self.hotel_results.pop(0) if len(self.hotel_results) > 1 else self.hotel_results[0]

    async def _forecast(self, city: str, startDate: str, endDate: str) -> str:
        self.calls.append("weather__forecast")
        day = {
            "date": (today() + timedelta(days=14)).isoformat(),
            "highC": 20,
            "lowC": 12,
            "rainChancePct": 30,
        }
        return json.dumps({"city": "Tokyo", "days": [day]})

    async def _attractions(self, city: str, interests: list[str] | None = None) -> str:
        self.calls.append("places__attractions")
        return ATTRACTIONS

    def classify(self, *items: Turn) -> None:
        queue_turns(self.provider.queues, *items)

    def script_flights(self) -> None:
        args = {"origin": "SIN", "destination": "TYO", "departDate": "d", "returnDate": "r"}
        self.provider.scripts["flights"] += [
            call_reply("flights__search", args),
            AIMessage("Nonstop is cheapest."),
        ]

    def script_hotels(self) -> None:
        args = {"city": "Tokyo", "checkIn": "a", "checkOut": "b"}
        self.provider.scripts["hotels"] += [
            call_reply("hotels__search", args),
            AIMessage("Central is handy."),
        ]

    def script_itinerary(self) -> None:
        forecast = {"city": "Tokyo", "startDate": "a", "endDate": "b"}
        self.provider.scripts["weather"] += [
            call_reply("weather__forecast", forecast),
            AIMessage("Mild."),
        ]
        self.provider.scripts["places"] += [
            call_reply("places__attractions", {"city": "Tokyo"}),
            AIMessage("Temples."),
        ]

    def script_everything(self) -> None:
        self.script_flights()
        self.script_hotels()
        self.script_itinerary()

    async def say(self, text: str, action: dict[str, Any] | None = None) -> list[Event]:
        kwargs = {"action": action} if action else {}
        message = HumanMessage(text, additional_kwargs=kwargs)
        stream: AsyncIterator[Any] = self.graph.astream(
            cast("PlannerState", {"messages": [message]}),
            self.config,
            stream_mode="custom",
            version="v2",
            subgraphs=True,
        )
        return [part["data"] async for part in stream]

    async def values(self) -> dict[str, Any]:
        return (await self.graph.aget_state(self.config)).values

    async def full_demo(
        self, text: str = "5 days in Tokyo from Singapore, food and hiking", **extra: Any
    ) -> list[Event]:
        details = {"destination": "Tokyo", "origin": "Singapore", "length_days": DAYS}
        self.classify(classification(**{**details, "interests": ["food", "hiking"], **extra}))
        self.script_everything()
        return await self.say(text)


def approve(card: dict[str, Any], flight: str = "f1", hotel: str = "h1") -> dict[str, Any]:
    return {"type": "approve", "cardId": card["id"], "flightId": flight, "hotelId": hotel}


async def test_missing_origin_is_asked_before_anything_is_searched() -> None:
    scenario = Scenario()
    scenario.classify(
        classification(destination="Tokyo", length_days=DAYS, interests=["food", "hiking"])
    )
    scenario.provider.scripts["medium"].append(AIMessage("Which city will you fly from?"))

    events = await scenario.say("5 days in Tokyo, mid-budget, food and hiking")

    values = await scenario.values()
    assert values["messages"][-1].text == "Which city will you fly from?"
    assert values["trip"]["redo"] == ["flights", "hotels", "itinerary"]
    assert "origin" not in values["trip"]["brief"]
    assert values.get("cards", []) == []
    assert scenario.calls == []
    assert of_kind(events, "plan_card") == []
    assert [pill["action"] for pill in of_kind(events, "pills")[0]["items"]] == [None, None]


async def test_answering_the_origin_runs_every_part_in_order_and_shows_one_card() -> None:
    scenario = Scenario()
    scenario.classify(
        classification(destination="Tokyo", length_days=DAYS, interests=["food", "hiking"]),
        classification(origin="Singapore"),
    )
    scenario.provider.scripts["medium"].append(AIMessage("Which city will you fly from?"))
    await scenario.say("5 days in Tokyo, mid-budget, food and hiking")
    scenario.script_everything()

    events = await scenario.say("from Singapore")

    values = await scenario.values()
    [card] = values["cards"]
    chips = [(e["name"], e["status"]) for e in of_kind(events, "tool_call")]
    assert chips[:4] == [
        ("flights__search", "started"),
        ("flights__search", "finished"),
        ("hotels__search", "started"),
        ("hotels__search", "finished"),
    ]
    assert {name for name, _ in chips[4:]} == {
        "weather__forecast",
        "places__attractions",
        "calculator",
    }
    assert scenario.calls.count("flights__search") == 1
    kinds = [e["event"] for e in events if e["event"] in {"tool_call", "plan_card", "pills"}]
    assert kinds[-2:] == ["plan_card", "pills"]
    assert card["status"] == "open"
    assert [option["id"] for option in card["flights"]] == ["f1", "f2", "f3"]
    assert [option["id"] for option in card["hotels"]] == ["h1", "h2", "h3"]
    assert card["picked"] == {"flightId": "f1", "hotelId": "h1"}
    assert card["flights"][0] == {
        "id": "f1",
        "summary": "Flight 1",
        "priceUsd": 450.0,
        "deepLink": "https://flight.example/f1",
    }
    assert len(card["days"]) == DAYS
    assert card["days"][0]["weather"] == "12 to 20 °C, 30% chance of rain"
    assert {day["weather"] for day in card["days"][1:]} == {NO_FORECAST}
    assert card["days"][0]["activities"] == ["Tsukiji market", "Hike Mt. Takao"]
    assert {day["costUsd"] for day in card["days"]} == {90.0}
    assert card["budget"] == {
        "flightUsd": 450.0,
        "hotelUsd": 600.0,
        "dailyUsd": 450.0,
        "totalUsd": 1500.0,
        "budgetUsd": None,
        "overBudget": False,
    }
    [shown] = of_kind(events, "plan_card")
    assert shown["id"] == card["id"]
    assert "messageId" not in shown
    last = values["messages"][-1]
    assert last.additional_kwargs["card_id"] == card["id"]
    assert [use["name"] for use in last.additional_kwargs["turn_tools"]][:2] == [
        "flights__search",
        "hotels__search",
    ]
    assert card["messageId"] == last.id


async def test_the_card_turn_ends_with_approve_reject_and_one_or_two_change_pills() -> None:
    scenario = Scenario()

    events = await scenario.full_demo()

    [pills] = of_kind(events, "pills")
    assert pills["items"] == [
        {"text": "Approve", "action": "approve"},
        {"text": "Reject", "action": "reject"},
        {"text": "Cheaper hotel", "action": None},
        {"text": "Add places", "action": None},
    ]
    assert (await scenario.values())["pills"] == pills["items"]


async def test_approve_records_the_picks_and_confirms_with_both_links() -> None:
    scenario = Scenario()
    await scenario.full_demo(budget_usd=1600)
    card = (await scenario.values())["cards"][0]
    assert card["budget"]["overBudget"] is False

    events = await scenario.say("Approve", approve(card, flight="f2", hotel="h3"))

    values = await scenario.values()
    [approved] = values["cards"]
    assert approved["status"] == "approved"
    assert approved["picked"] == {"flightId": "f2", "hotelId": "h3"}
    assert approved["budget"] == {
        "flightUsd": 520.0,
        "hotelUsd": 800.0,
        "dailyUsd": 450.0,
        "totalUsd": 1770.0,
        "budgetUsd": 1600.0,
        "overBudget": True,
    }
    text = values["messages"][-1].text
    assert "Flight 2" in text
    assert "https://flight.example/f2" in text
    assert "Hotel 3" in text
    assert "https://hotel.example/h3" in text
    assert "".join(e["text"] for e in of_kind(events, "token")) == text
    assert of_kind(events, "plan_card") == []
    assert [pill["action"] for pill in of_kind(events, "pills")[0]["items"]] == [None, None]
    assert scenario.provider.structured_calls.count("Classification") == 1


async def test_an_action_turn_skips_the_classify_model_and_the_search() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    card = (await scenario.values())["cards"][0]
    searches = len(scenario.calls)
    classifications = scenario.provider.structured_calls.count("Classification")

    await scenario.say("Approve", approve(card))

    assert scenario.provider.structured_calls.count("Classification") == classifications
    assert len(scenario.calls) == searches


async def test_an_approve_from_a_replaced_card_reshows_the_current_card_with_a_note() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    first = (await scenario.values())["cards"][0]
    scenario.classify(classification(change="hotel", hotel_criteria="cheaper"))
    scenario.script_hotels()
    await scenario.say("a cheaper hotel")

    events = await scenario.say("Approve", approve(first))

    values = await scenario.values()
    assert [card["status"] for card in values["cards"]] == ["replaced", "replaced", "open"]
    current = values["cards"][2]
    assert values["messages"][-1].text.startswith(NOTE_OLD)
    [shown] = of_kind(events, "plan_card")
    assert shown["id"] == current["id"]
    assert shown["id"] not in {card["id"] for card in values["cards"][:2]}
    assert shown["flights"] == values["cards"][1]["flights"]
    assert of_kind(events, "pills")[0]["items"][0] == {"text": "Approve", "action": "approve"}


async def test_an_approve_with_ids_not_on_the_card_changes_nothing() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    card = (await scenario.values())["cards"][0]

    events = await scenario.say("Approve", approve(card, flight="f9"))

    values = await scenario.values()
    assert [c["status"] for c in values["cards"]] == ["replaced", "open"]
    assert values["messages"][-1].text.startswith(NOTE_WRONG_PICK)
    assert [e["id"] for e in of_kind(events, "plan_card")] == [values["cards"][1]["id"]]
    assert values["cards"][1]["id"] != card["id"]


async def test_an_approve_of_an_already_approved_card_leaves_it_alone_with_a_note() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    card = (await scenario.values())["cards"][0]
    await scenario.say("Approve", approve(card, flight="f2"))

    await scenario.say("Approve", approve(card, flight="f3"))

    values = await scenario.values()
    assert [c["status"] for c in values["cards"]] == ["approved"]
    assert values["cards"][0]["picked"]["flightId"] == "f2"
    assert values["messages"][-1].text == "This trip is already approved."


async def test_an_approve_with_no_card_at_all_answers_with_the_note_alone() -> None:
    scenario = Scenario()
    action = {"type": "approve", "cardId": "ghost", "flightId": "f1", "hotelId": "h1"}

    events = await scenario.say("Approve", action)

    values = await scenario.values()
    assert values.get("cards", []) == []
    assert values["messages"][-1].text.startswith(NOTE_NO_CARD)
    assert of_kind(events, "plan_card") == []
    assert "card_id" not in values["messages"][-1].additional_kwargs
    assert scenario.calls == []


async def test_reject_asks_what_to_change_and_searches_nothing() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    card = (await scenario.values())["cards"][0]
    searches = len(scenario.calls)
    scenario.provider.scripts["medium"].append(AIMessage("What would you like to change?"))

    events = await scenario.say("Reject", {"type": "reject", "cardId": card["id"]})

    values = await scenario.values()
    assert values["messages"][-1].text == "What would you like to change?"
    assert len(values["cards"]) == 1
    assert values["cards"][0]["status"] == "open"
    assert len(scenario.calls) == searches
    assert of_kind(events, "plan_card") == []
    assert [pill["action"] for pill in of_kind(events, "pills")[0]["items"]] == [None, None]


async def test_a_hotel_change_redoes_only_the_hotels_and_keeps_flights_and_days() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    first = (await scenario.values())["cards"][0]
    scenario.hotel_results = [CHEAPER_HOTELS]
    scenario.classify(classification(change="hotel", hotel_criteria="cheaper"))
    scenario.script_hotels()

    events = await scenario.say("a cheaper hotel please")

    values = await scenario.values()
    second = values["cards"][1]
    assert [card["status"] for card in values["cards"]] == ["replaced", "open"]
    assert scenario.calls.count("flights__search") == 1
    assert scenario.calls.count("hotels__search") == 2
    assert scenario.calls.count("weather__forecast") == 1
    assert scenario.calls.count("places__attractions") == 1
    assert second["flights"] == first["flights"]
    assert second["days"] == first["days"]
    assert [option["id"] for option in second["hotels"]] == ["h5", "h1", "h2"]
    assert second["picked"] == {"flightId": "f1", "hotelId": "h1"}
    assert [e["id"] for e in of_kind(events, "plan_card")] == [second["id"]]
    requests = [
        message.text
        for seen in scenario.provider.seen
        for message in seen
        if isinstance(message, HumanMessage) and "Search hotels" in message.text
    ]
    assert "cheaper" in requests[-1]


EVERYTHING = {"flights": 2, "hotels": 2, "weather": 2, "places": 2}
FLIGHTS_ONLY = {"flights": 2, "hotels": 1, "weather": 1, "places": 1}
ITINERARY_ONLY = {"flights": 1, "hotels": 1, "weather": 2, "places": 2}
FLIGHTS_AND_ITINERARY = {"flights": 2, "hotels": 1, "weather": 2, "places": 2}
HOTELS_ONLY = {"flights": 1, "hotels": 2, "weather": 1, "places": 1}


@pytest.mark.parametrize(
    ("update", "length", "searched"),
    [
        ({"length_days": 7}, 7, EVERYTHING),
        ({"destination": "Osaka"}, DAYS, EVERYTHING),
        ({"travellers": 2}, DAYS, EVERYTHING),
        ({"origin": "Hanoi"}, DAYS, FLIGHTS_ONLY),
        ({"interests": ["museums"]}, DAYS, ITINERARY_ONLY),
        ({"budget_usd": 3000, "budget_focus": "hotels"}, DAYS, HOTELS_ONLY),
        ({"budget_usd": 3000, "budget_focus": "places"}, DAYS, ITINERARY_ONLY),
        ({"budget_usd": 3000, "budget_focus": "flights"}, DAYS, FLIGHTS_AND_ITINERARY),
        ({"flight_criteria": "nonstop"}, DAYS, FLIGHTS_AND_ITINERARY),
        ({"flight_criteria": "nonstop", "hotel_criteria": "cheaper"}, DAYS, EVERYTHING),
    ],
)
async def test_each_change_redoes_exactly_its_parts_and_ends_in_a_new_card(
    update: dict[str, Any], length: int, searched: dict[str, int]
) -> None:
    scenario = Scenario()
    await scenario.full_demo()
    scenario.classify(classification(**update))
    if searched["flights"] == 2:
        scenario.script_flights()
    if searched["hotels"] == 2:
        scenario.script_hotels()
    if searched["weather"] == 2:
        scenario.script_itinerary()

    words = [
        "make it 7 days" if "length_days" in update else "change it",
        "a nonstop flight" if "flight_criteria" in update else "",
        "a cheaper hotel" if "hotel_criteria" in update else "",
    ]

    events = await scenario.say(" and ".join(word for word in words if word))

    values = await scenario.values()
    assert [card["status"] for card in values["cards"]] == ["replaced", "open"]
    assert len(values["cards"][1]["days"]) == length
    assert len(of_kind(events, "plan_card")) == 1
    assert {
        "flights": scenario.calls.count("flights__search"),
        "hotels": scenario.calls.count("hotels__search"),
        "weather": scenario.calls.count("weather__forecast"),
        "places": scenario.calls.count("places__attractions"),
    } == searched
    assert values["trip"]["redo"] == []


async def test_a_requested_hotel_change_does_not_hide_a_flight_criteria_change() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    scenario.classify(classification(change="hotel", flight_criteria="nonstop"))
    scenario.script_flights()
    scenario.script_hotels()
    scenario.script_itinerary()

    await scenario.say("a nonstop flight and a different hotel")

    assert scenario.calls.count("flights__search") == 2
    assert scenario.calls.count("hotels__search") == 2
    assert scenario.calls.count("weather__forecast") == 2


async def test_a_budget_only_message_with_copied_criteria_asks_and_keeps_the_hotels() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    scenario.classify(classification(change="hotel", hotel_criteria="cheaper"))
    scenario.script_hotels()
    await scenario.say("A cheaper hotel, please.")
    assert scenario.calls.count("hotels__search") == 2
    scenario.classify(classification(hotel_criteria="Cheaper ", budget_level="high"))
    scenario.provider.scripts["medium"].append(AIMessage("Where should the money go?"))

    await scenario.say("Change only the budget level to high.")

    values = await scenario.values()
    assert scenario.calls.count("hotels__search") == 2
    assert scenario.calls.count("flights__search") == 1
    assert len(values["cards"]) == 2
    assert values["messages"][-1].text == "Where should the money go?"


async def test_a_nonstop_flights_message_re_searches_the_flights() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    scenario.classify(classification(change="flights", flight_criteria="nonstop"))
    scenario.script_flights()
    scenario.script_itinerary()

    await scenario.say("nonstop flights please")

    assert scenario.calls.count("flights__search") == 2
    assert scenario.calls.count("hotels__search") == 1


async def test_a_budget_change_without_a_focus_asks_where_the_money_goes() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    searches = len(scenario.calls)
    scenario.classify(classification(budget_usd=3000))
    scenario.provider.scripts["medium"].append(AIMessage("Where should the extra money go?"))

    events = await scenario.say("raise the budget to $3000")

    values = await scenario.values()
    assert len(scenario.calls) == searches
    assert [card["status"] for card in values["cards"]] == ["open"]
    assert values["trip"]["brief"]["budget_usd"] == 3000
    assert values["messages"][-1].text == "Where should the extra money go?"
    assert of_kind(events, "plan_card") == []


async def test_naming_the_focus_after_the_question_re_plans_that_part_with_the_budget() -> None:
    scenario = Scenario()
    await scenario.full_demo()
    scenario.classify(classification(budget_usd=3000))
    scenario.provider.scripts["medium"].append(AIMessage("Where should the extra money go?"))
    await scenario.say("raise the budget to $3000")
    scenario.classify(classification(budget_focus="hotels"))
    scenario.script_hotels()

    await scenario.say("the hotel")

    values = await scenario.values()
    assert scenario.calls.count("hotels__search") == 2
    assert scenario.calls.count("flights__search") == 1
    assert [card["status"] for card in values["cards"]] == ["replaced", "open"]
    assert values["cards"][1]["budget"]["budgetUsd"] == 3000
    requests = [
        message.text
        for seen in scenario.provider.seen
        for message in seen
        if isinstance(message, HumanMessage) and "Search hotels" in message.text
    ]
    assert "The trip budget is $3,000 in total." in requests[-1]
    assert "The user wants the hotel to reflect this budget." in requests[-1]


async def test_a_budget_level_keeps_the_options_moves_the_daily_costs_and_sets_no_amount() -> None:
    scenario = Scenario()
    await scenario.full_demo(budget_level="high")

    [card] = (await scenario.values())["cards"]
    assert {day["costUsd"] for day in card["days"]} == {180.0}
    assert card["budget"]["dailyUsd"] == 900.0
    assert card["budget"]["budgetUsd"] is None


async def test_over_budget_warns_with_the_breakdown_and_swaps_nothing() -> None:
    scenario = Scenario()

    await scenario.full_demo(budget_usd=600)

    values = await scenario.values()
    [card] = values["cards"]
    text = values["messages"][-1].text
    assert card["budget"]["overBudget"] is True
    assert card["budget"]["budgetUsd"] == 600
    assert card["picked"] == {"flightId": "f1", "hotelId": "h1"}
    breakdown = (
        "$450.00 flight + $600.00 hotel + $450.00 daily costs = $1,500.00 total "
        "against a budget of $600.00"
    )
    assert "Warning: this is over budget" in text
    assert breakdown in text
    assert "Nothing was changed" in text


async def test_a_trip_beyond_the_forecast_range_skips_weather_and_still_plans() -> None:
    scenario = Scenario()
    start = today() + timedelta(days=60)
    end = start + timedelta(days=DAYS - 1)

    events = await scenario.full_demo(
        f"Tokyo from Singapore, from {start.isoformat()} to {end.isoformat()}",
        length_days=None,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )

    values = await scenario.values()
    [card] = values["cards"]
    assert "weather__forecast" not in scenario.calls
    assert "places__attractions" in scenario.calls
    assert {day["weather"] for day in card["days"]} == {NO_FORECAST}
    assert of_kind(events, "pills")[0]["items"][0]["action"] == "approve"


async def test_a_missing_hotel_key_leaves_hotels_empty_with_a_notice_and_no_approve() -> None:
    scenario = Scenario()
    message = "Hotels data is unavailable: no key set."
    scenario.hotel_error = ToolException(message)

    events = await scenario.full_demo()

    values = await scenario.values()
    [card] = values["cards"]
    assert card["hotels"] == []
    assert card["picked"]["hotelId"] == ""
    assert [option["id"] for option in card["flights"]] == ["f1", "f2", "f3"]
    assert {"event": "notice", "message": message} in events
    assert "I could not find hotel options right now." in values["messages"][-1].text
    assert [pill["action"] for pill in of_kind(events, "pills")[0]["items"]] == [None, None]


def test_build_days_places_each_draft_on_its_date_and_leaves_a_missing_date_empty() -> None:
    draft = itinerary_module.ItineraryDraft(
        days=[
            itinerary_module.DayDraft(date="2026-11-03", activities=["Market"]),
            itinerary_module.DayDraft(date="2026-11-01", activities=["Temple", " "]),
        ]
    )

    days = itinerary_module.build_days(["2026-11-01", "2026-11-02", "2026-11-03"], draft, 90.0)

    assert [day["activities"] for day in days] == [["Temple"], [], ["Market"]]


def test_build_days_falls_back_to_the_order_when_a_draft_has_no_known_date() -> None:
    draft = itinerary_module.ItineraryDraft(
        days=[
            itinerary_module.DayDraft(date="2026-11-02", activities=["Hike"]),
            itinerary_module.DayDraft(date="day one", activities=["Temple"]),
        ]
    )

    days = itinerary_module.build_days(["2026-11-01", "2026-11-02"], draft, 90.0)

    assert [day["activities"] for day in days] == [["Temple"], ["Hike"]]
