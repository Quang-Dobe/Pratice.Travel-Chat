import json
from collections.abc import Sequence
from typing import Any, cast

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage, ToolCall
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from src.graph.answer import FETCH_REFUSED, fetch_guard, pin_reviews
from src.graph.deps import PlannerDeps
from src.graph.state import Picked, PlanCard, PlannerState
from src.settings import Settings, Tier
from src.utils.reviews import (
    MAX_REVIEW_PINS,
    PinName,
    PinNames,
    hits_listing,
    located_pins,
    merge_pins,
    pin_names,
    pin_query,
    sources,
)
from src.utils.tools import RawCall, Tools
from src.utils.trip import Budget, ReviewPin

SEARCH = "search__web"
FETCH = "fetch__page"


class FakeChat(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError("the fake chat is never invoked directly")


class FakeProvider:
    def __init__(self, outcome: PinNames | Exception) -> None:
        self.outcome = outcome
        self.structured_calls = 0
        self.prompts: list[list[BaseMessage]] = []

    @property
    def errors(self) -> type[Exception]:
        return RuntimeError

    def model(self, tier: Tier) -> BaseChatModel:
        return FakeChat()

    def system(self, prompt: str, hint: str) -> SystemMessage:
        return SystemMessage(f"{prompt}\n{hint}")

    def with_tools(
        self, model: BaseChatModel, tools: Sequence[BaseTool], final: bool
    ) -> Runnable[Any, Any]:
        raise NotImplementedError

    def structured(self, model: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, Any]:
        def pick(messages: list[BaseMessage]) -> BaseModel:
            self.structured_calls += 1
            self.prompts.append(messages)
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

        return RunnableLambda(pick)


def hit(title: str, url: str) -> dict[str, str]:
    return {"title": title, "url": url, "snippet": "A snippet."}


def search_ok(*hits: dict[str, str]) -> RawCall:
    return (SEARCH, json.dumps(list(hits)), True)


def page_ok(url: str, title: str = "Page") -> RawCall:
    return (FETCH, json.dumps({"title": title, "url": url, "text": "Words."}), True)


def urls(items: list[Any]) -> list[list[str]]:
    return [[link["url"] for link in item["links"]] for item in items]


def test_links_come_only_from_ok_raw_results() -> None:
    failed: RawCall = (SEARCH, json.dumps([hit("Bad", "https://bad.example/1")]), False)
    stray_hit = json.dumps([hit("Stray", "https://stray.example")])
    stray: RawCall = ("weather__forecast", stray_hit, True)
    calls = [failed, stray, search_ok(hit("Good", "https://good.example/1"))]

    items = sources([("Hotel Gracery Tokyo", calls)], [])

    assert items == [
        {
            "subject": "Hotel Gracery Tokyo",
            "links": [{"title": "Good", "url": "https://good.example/1"}],
        }
    ]


def test_a_page_that_failed_to_load_is_never_a_link() -> None:
    failed: RawCall = (FETCH, "Page could not be read.", False)
    calls = [search_ok(hit("A", "https://a.example/1"))]

    items = sources([("subject", calls)], [failed, (FETCH, "not json", True)])

    assert urls(cast("list[Any]", items)) == [["https://a.example/1"]]


def test_only_https_links_are_kept() -> None:
    calls = [
        search_ok(
            hit("Plain", "http://plain.example/1"),
            hit("Script", "javascript:alert(1)"),
            hit("Ftp", "ftp://files.example/1"),
            hit("Bare", "https://"),
            hit("Spaced", "https://spaced.example/a b"),
            hit("Tagged", "https://tagged.example/<b>"),
            hit("Secure", "https://secure.example/1"),
        )
    ]

    items = sources([("subject", calls)], [])

    assert urls(cast("list[Any]", items)) == [["https://secure.example/1"]]


def test_a_link_is_listed_once_and_at_most_three_are_kept() -> None:
    calls = [
        search_ok(
            hit("One", "https://one.example"),
            hit("One again", "https://one.example"),
            hit("Two", "https://two.example"),
            hit("Three", "https://three.example"),
            hit("Four", "https://four.example"),
        ),
        search_ok(hit("Two again", "https://two.example"), hit("Five", "https://five.example")),
    ]

    items = sources([("subject", calls)], [])

    assert urls(cast("list[Any]", items)) == [
        ["https://one.example", "https://two.example", "https://three.example"]
    ]
    assert items[0]["links"][0]["title"] == "One"


def test_pages_that_were_read_come_before_the_other_hits() -> None:
    calls = [
        search_ok(
            hit("H1", "https://h1.example"),
            hit("H2", "https://h2.example"),
            hit("H3", "https://h3.example"),
            hit("H4", "https://h4.example"),
        )
    ]
    pages = [
        page_ok("https://h3.example", "Page three"),
        page_ok("https://elsewhere.example"),
        page_ok("https://h2.example", "Page two"),
    ]

    items = sources([("subject", calls)], pages)

    assert urls(cast("list[Any]", items)) == [
        ["https://h3.example", "https://h2.example", "https://h1.example"]
    ]
    assert items[0]["links"][0]["title"] == "H3"


def test_a_failed_or_empty_search_adds_no_item() -> None:
    failed: RawCall = (SEARCH, "Search is unavailable: no key set.", False)
    empty: RawCall = (SEARCH, "[]", True)

    garbled: RawCall = (SEARCH, "not json", True)
    searches: list[tuple[str, list[RawCall]]] = [
        ("first", [failed]),
        ("second", [empty]),
        ("third", []),
        ("fourth", [garbled]),
    ]

    items = sources(searches, [page_ok("https://a.example")])

    assert items == []


def test_each_search_request_is_its_own_item_in_order() -> None:
    first = [search_ok(hit("A", "https://a.example"))]
    second = [search_ok(hit("B", "https://b.example"))]
    failed: list[RawCall] = [(SEARCH, "boom", False)]

    items = sources([("Hotel A", first), ("Ramen B", failed), ("Ramen C", second)], [])

    assert [item["subject"] for item in items] == ["Hotel A", "Ramen C"]


def test_titles_and_subjects_are_cleaned_and_a_missing_title_falls_back_to_the_link() -> None:
    messy = hit("  Great\n  stay \t here ", "https://a.example")
    untitled = hit("", "https://b.example")
    calls = [search_ok(messy, untitled)]

    [item] = sources([("   ", calls)], [])

    assert item["subject"] == "Reviews"
    assert item["links"] == [
        {"title": "Great stay here", "url": "https://a.example"},
        {"title": "https://b.example", "url": "https://b.example"},
    ]


def test_search_results_wrapped_in_data_are_read_too() -> None:
    wrapped: RawCall = (SEARCH, json.dumps({"data": [hit("A", "https://a.example")]}), True)

    items = sources([("subject", [wrapped])], [])

    assert urls(cast("list[Any]", items)) == [["https://a.example"]]


def test_the_model_sees_titles_escaped_and_links_untouched() -> None:
    calls = [search_ok(hit("<b>Fun</b> & food", "https://a.example/?x=1&y=2"))]

    listing = hits_listing(calls)

    assert listing == (
        "<results>\n&lt;b&gt;Fun&lt;/b&gt; &amp; food - https://a.example/?x=1&y=2\n</results>"
    )
    assert hits_listing([(SEARCH, "Search failed", False)]) == ""
    assert hits_listing([search_ok(hit("Plain", "http://plain.example"))]) == ""


async def test_pin_names_returns_at_most_eight_unique_names() -> None:
    items = [PinName(name=f"Place {index}", kind="place") for index in range(12)]
    items.insert(1, PinName(name="  place 0 ", kind="restaurant"))
    items.insert(2, PinName(name="   ", kind="place"))
    provider = FakeProvider(PinNames(items=items))

    names = await pin_names(provider, FakeChat(), "Reply about places.", "Tokyo")

    assert [name.name for name in names] == [f"Place {index}" for index in range(8)]
    assert names[0].kind == "place"
    assert provider.structured_calls == 1


async def test_pin_names_sends_the_reply_and_destination_as_tagged_data() -> None:
    provider = FakeProvider(PinNames(items=[]))

    names = await pin_names(provider, FakeChat(), "Ichiran <ramen>", "Tokyo")

    assert names == []
    prompt = provider.prompts[0][-1].text
    assert "<destination>Tokyo</destination>" in prompt
    assert "<reply>Ichiran &lt;ramen&gt;</reply>" in prompt


async def test_pin_names_makes_no_call_for_an_empty_reply() -> None:
    provider = FakeProvider(PinNames(items=[PinName(name="X", kind="place")]))

    assert await pin_names(provider, FakeChat(), "   ", "Tokyo") == []
    assert provider.structured_calls == 0


async def test_pin_names_failure_gives_no_names() -> None:
    provider = FakeProvider(RuntimeError("model down"))

    assert await pin_names(provider, FakeChat(), "Ichiran is good.", "Tokyo") == []


def pin(name: str, kind: str = "place") -> ReviewPin:
    return cast("ReviewPin", {"name": name, "kind": kind, "lat": 1.0, "lon": 2.0})


def make_card(pins: list[ReviewPin] | None = None) -> PlanCard:
    budget = Budget(
        flightUsd=0.0, hotelUsd=0.0, dailyUsd=0.0, totalUsd=0.0, budgetUsd=None, overBudget=False
    )
    card = PlanCard(
        id="c1",
        messageId="m1",
        status="open",
        flights=[],
        hotels=[],
        picked=Picked(flightId="", hotelId=""),
        days=[],
        budget=budget,
    )
    if pins is not None:
        card["reviewPins"] = pins
    return card


def test_located_pins_keep_only_the_names_that_were_found() -> None:
    candidates = [PinName(name="Ichiran", kind="restaurant"), PinName(name="Ghost", kind="place")]
    found = {pin_query("Ichiran", "Tokyo"): {"lat": 35.6, "lon": 139.7}}

    pins = located_pins(candidates, found, "Tokyo")

    assert pins == [{"name": "Ichiran", "kind": "restaurant", "lat": 35.6, "lon": 139.7}]
    assert pin_query("Ichiran", "") == "Ichiran"


def test_merge_pins_skips_names_already_pinned_ignoring_case() -> None:
    card = make_card([pin("Ichiran")])

    merged, added = merge_pins(card, [pin("ichiran"), pin("Senso-ji"), pin(" Senso-ji ")])

    assert [item["name"] for item in added] == ["Senso-ji"]
    assert [item["name"] for item in merged["reviewPins"]] == ["Ichiran", "Senso-ji"]


def test_merge_pins_never_changes_the_card_it_was_given() -> None:
    card = make_card([pin("Ichiran")])

    merged, _ = merge_pins(card, [pin("Senso-ji")])

    assert [item["name"] for item in card["reviewPins"]] == ["Ichiran"]
    assert merged is not card


def test_merge_pins_caps_the_card_at_twenty_pins() -> None:
    card = make_card([pin(f"Old {index}") for index in range(18)])

    merged, added = merge_pins(card, [pin(f"New {index}") for index in range(5)])

    assert MAX_REVIEW_PINS == 20
    assert [item["name"] for item in added] == ["New 0", "New 1"]
    assert len(merged["reviewPins"]) == 20
    assert merge_pins(merged, [pin("One more")])[1] == []


def test_a_card_without_review_pins_gets_them() -> None:
    card = make_card()
    assert "reviewPins" not in card

    merged, added = merge_pins(card, [pin("Ichiran")])

    assert merged["reviewPins"] == added == [pin("Ichiran")]


def fetch_call(url: object, name: str = FETCH) -> ToolCall:
    return ToolCall(name=name, args={"url": url}, id="c1")


def test_the_fetch_guard_unescapes_a_hit_url_and_passes_it_on() -> None:
    hit_url = "https://example.com/r?id=1&lang=en"
    check = fetch_guard({hit_url})

    for sent in (hit_url, "https://example.com/r?id=1&amp;lang=en", f" {hit_url} "):
        passed = check(fetch_call(sent))
        assert not isinstance(passed, str)
        assert passed["args"] == {"url": hit_url}
        assert passed["id"] == "c1"


def test_the_fetch_guard_refuses_anything_search_did_not_return() -> None:
    check = fetch_guard({"https://hit.example/a"})

    for sent in ("https://evil.example/", "http://hit.example/a", "", None, 7):
        assert check(fetch_call(sent)) == FETCH_REFUSED
    assert fetch_guard(set())(fetch_call("https://hit.example/a")) == FETCH_REFUSED


def test_the_fetch_guard_leaves_other_tools_alone() -> None:
    call = fetch_call("anything", name="search__web")

    assert fetch_guard(set())(call) == call


def make_deps(provider: FakeProvider, tools: Tools | None = None) -> PlannerDeps:
    settings = Settings(database_url="postgresql://unused", jwks_url="http://unused")
    return PlannerDeps(provider=provider, settings=settings, tools=tools or Tools())


def make_state(*cards: PlanCard) -> PlannerState:
    brief = {"destination": "Tokyo"}
    return cast("PlannerState", {"messages": [], "cards": list(cards), "trip": {"brief": brief}})


async def test_with_no_card_there_is_no_pin_call() -> None:
    provider = FakeProvider(PinNames(items=[PinName(name="Ichiran", kind="restaurant")]))

    pinned, uses = await pin_reviews(make_state(), make_deps(provider), "Ichiran is good.")

    assert (pinned, uses) == ([], [])
    assert provider.structured_calls == 0


async def test_names_that_cannot_be_placed_add_no_pins_and_no_event() -> None:
    sent: list[list[str]] = []

    async def geocode(places: list[str]) -> str:
        sent.append(places)
        return "[]"

    tool = StructuredTool.from_function(
        coroutine=geocode, name="geo__geocode", description="Geocode."
    )
    provider = FakeProvider(PinNames(items=[PinName(name="Ichiran", kind="restaurant")]))
    deps = make_deps(provider, Tools(domains={"geo": [tool]}))

    pinned, uses = await pin_reviews(make_state(make_card()), deps, "Ichiran is good.")

    assert pinned == []
    assert uses == [{"name": "geo__geocode", "kind": "retrieve"}]
    assert sent == [["Ichiran, Tokyo"]]


async def test_a_reply_that_names_nothing_costs_one_call_and_no_lookup() -> None:
    provider = FakeProvider(PinNames(items=[]))

    pinned, uses = await pin_reviews(
        make_state(make_card()), make_deps(provider), "No places named."
    )

    assert (pinned, uses) == ([], [])
    assert provider.structured_calls == 1
