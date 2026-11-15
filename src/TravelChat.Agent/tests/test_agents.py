import copy
from collections.abc import Sequence
from html import unescape
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolCall, ToolMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel

from src.agents.base import MAX_ROUNDS, NO_TOOLS
from src.agents.registry import SPECS, build_agents
from src.settings import Settings, Tier
from src.utils.tools import Tools


class FakeChat(BaseChatModel):
    tier: str = ""

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
        raise RuntimeError("the fake chat is only used through with_tools")


class FakeProvider:
    def __init__(self, script: list[AIMessage]) -> None:
        self.script = script
        self.binds: list[tuple[list[str], bool]] = []
        self.seen: list[list[BaseMessage]] = []
        self.bound_tiers: list[str] = []

    @property
    def errors(self) -> type[Exception]:
        return RuntimeError

    def model(self, tier: Tier) -> BaseChatModel:
        return FakeChat(tier=tier)

    def system(self, prompt: str, hint: str) -> SystemMessage:
        return SystemMessage(f"{prompt}\n{hint}")

    def with_tools(
        self, model: BaseChatModel, tools: Sequence[BaseTool], final: bool
    ) -> Runnable[Any, Any]:
        self.binds.append(([tool.name for tool in tools], final))
        self.bound_tiers.append(model.tier if isinstance(model, FakeChat) else "")

        async def reply(messages: list[BaseMessage]) -> AIMessage:
            self.seen.append(list(messages))
            return self.script.pop(0)

        return RunnableLambda(reply)

    def structured(self, model: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, Any]:
        raise NotImplementedError


def make_settings() -> Settings:
    return Settings(database_url="postgresql://unused", jwks_url="http://unused")


def tool_reply(name: str, args: dict[str, Any], call_id: str = "call-1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def forecast_tool(executed: list[str]) -> BaseTool:
    async def forecast(city: str) -> str:
        executed.append(city)
        return f"Sunny in {city}"

    return StructuredTool.from_function(
        coroutine=forecast, name="weather__forecast", description="Forecast."
    )


def flights_tool(executed: list[str]) -> BaseTool:
    async def search(origin: str) -> str:
        executed.append(origin)
        return "[]"

    return StructuredTool.from_function(
        coroutine=search, name="flights__search", description="Flights."
    )


def toolbox(executed: list[str]) -> Tools:
    return Tools(
        domains={
            "weather": [forecast_tool(executed)],
            "flights": [flights_tool(executed)],
            "places": [],
            "hotels": [],
        }
    )


async def test_an_agent_only_binds_and_runs_its_own_domain_tools() -> None:
    executed: list[str] = []
    provider = FakeProvider(
        [tool_reply("weather__forecast", {"city": "Tokyo"}), AIMessage("Sunny.")]
    )
    agents = build_agents(provider, toolbox(executed), make_settings())

    result = await agents["weather"].run("forecast for Tokyo")

    assert set(agents) == {"flights", "hotels", "weather", "places", "search", "fetch", "artifact"}
    assert provider.binds == [(["weather__forecast"], False)] * 2
    assert executed == ["Tokyo"]
    assert result.summary == "Sunny."
    assert result.calls == [("weather__forecast", "Sunny in Tokyo", True)]


async def test_an_agent_binds_only_the_tools_its_spec_lists() -> None:
    executed: list[str] = []
    extra = forecast_tool(executed).model_copy(update={"name": "weather__history"})
    tools = toolbox(executed)
    tools.domains["weather"].append(extra)
    provider = FakeProvider([AIMessage("Sunny.")])
    agents = build_agents(provider, tools, make_settings())

    await agents["weather"].run("forecast for Tokyo")

    assert provider.binds == [(["weather__forecast"], False)]


def test_each_spec_names_its_agent_and_prompts_with_its_tools() -> None:
    for spec in SPECS:
        assert spec.name == f"{spec.domain}_agent"
        assert all(tool.name.startswith(f"{spec.domain}__") for tool in spec.tools)
        tools = spec.prompt().split("<tools>")[1].split("</tools>")[0]
        assert all(f"- {tool.name}: " in tools for tool in spec.tools)
        assert spec.when_to_use in spec.tool_description()


async def test_a_call_to_another_domains_tool_is_refused_and_never_run() -> None:
    executed: list[str] = []
    provider = FakeProvider(
        [tool_reply("flights__search", {"origin": "SIN"}), AIMessage("I could not.")]
    )
    agents = build_agents(provider, toolbox(executed), make_settings())

    result = await agents["weather"].run("find flights")

    assert executed == []
    assert result.calls == [("flights__search", "Unknown tool flights__search.", False)]
    refusal = provider.seen[1][-1]
    assert isinstance(refusal, ToolMessage)
    assert refusal.status == "error"


async def test_the_loop_stops_after_six_rounds_and_then_forbids_tool_calls() -> None:
    executed: list[str] = []
    script = [tool_reply("weather__forecast", {"city": f"City{i}"}, f"c{i}") for i in range(6)]
    provider = FakeProvider([*script, AIMessage("Enough.")])
    agents = build_agents(provider, toolbox(executed), make_settings())

    result = await agents["weather"].run("tour the world")

    assert MAX_ROUNDS == 6
    assert len(executed) == 6
    assert [final for _, final in provider.binds] == [False] * 6 + [True]
    assert result.summary == "Enough."
    assert len(result.calls) == 6


async def test_a_final_round_that_still_asks_for_a_tool_ends_with_its_text() -> None:
    executed: list[str] = []
    script = [tool_reply("weather__forecast", {"city": f"City{i}"}, f"c{i}") for i in range(7)]
    provider = FakeProvider(script)
    agents = build_agents(provider, toolbox(executed), make_settings())

    result = await agents["weather"].run("tour the world")

    assert len(executed) == 6
    assert result.summary == ""


async def test_a_tool_error_reaches_the_model_as_an_error_result_and_is_recorded() -> None:
    async def broken(city: str) -> str:
        raise ToolException("Weather data is unavailable: no key set.")

    tool = StructuredTool.from_function(
        coroutine=broken, name="weather__forecast", description="Forecast.", handle_tool_error=True
    )
    provider = FakeProvider(
        [tool_reply("weather__forecast", {"city": "Tokyo"}), AIMessage("No weather data.")]
    )
    agents = build_agents(provider, Tools(domains={"weather": [tool]}), make_settings())

    result = await agents["weather"].run("forecast")

    assert result.calls == [
        ("weather__forecast", "Weather data is unavailable: no key set.", False)
    ]
    seen = provider.seen[1][-1]
    assert isinstance(seen, ToolMessage)
    assert seen.status == "error"
    assert result.summary == "No weather data."


async def test_an_agent_without_tools_answers_without_calling_the_model() -> None:
    provider = FakeProvider([])
    agents = build_agents(provider, Tools(domains={}), make_settings())

    result = await agents["places"].run("attractions")

    assert result.summary == NO_TOOLS
    assert result.calls == []
    assert provider.binds == []


async def test_as_tool_returns_the_summary_and_the_raw_calls_as_the_artifact() -> None:
    executed: list[str] = []
    provider = FakeProvider(
        [tool_reply("weather__forecast", {"city": "Tokyo"}), AIMessage("Sunny.")]
    )
    agent = build_agents(provider, toolbox(executed), make_settings())["weather"]
    tool = agent.as_tool()

    message = await tool.ainvoke(
        {"name": "weather_agent", "args": {"request": "Tokyo"}, "id": "t1", "type": "tool_call"}
    )

    assert tool.name == "weather_agent"
    assert isinstance(message, ToolMessage)
    assert message.content == "Sunny."
    assert message.artifact == [("weather__forecast", "Sunny in Tokyo", True)]


def search_tool(executed: list[str]) -> BaseTool:
    async def search(query: str) -> str:
        executed.append(query)
        return "[]"

    return StructuredTool.from_function(coroutine=search, name="search__web", description="Web.")


def fetch_tool(executed: list[str]) -> BaseTool:
    async def read(url: str) -> str:
        executed.append(url)
        return "{}"

    return StructuredTool.from_function(coroutine=read, name="fetch__page", description="Page.")


def test_the_search_and_fetch_specs_run_on_the_helper_tier_with_a_call_cap() -> None:
    specs = {spec.domain: spec for spec in SPECS}
    search = specs["search"]
    fetch = specs["fetch"]

    assert (search.name, search.tier, search.max_calls) == ("search_agent", "helper", 2)
    assert (fetch.name, fetch.tier, fetch.max_calls) == ("fetch_agent", "helper", 3)
    assert [tool.name for tool in specs["search"].tools] == ["search__web"]
    assert [tool.name for tool in specs["fetch"].tools] == ["fetch__page"]
    assert all(spec.tier == "answer" and spec.max_calls is None for spec in SPECS[:4])


async def test_each_agent_gets_the_model_of_its_tier() -> None:
    searched: list[str] = []
    read: list[str] = []
    tools = Tools(
        domains={
            "weather": [forecast_tool([])],
            "search": [search_tool(searched)],
            "fetch": [fetch_tool(read)],
        }
    )
    settings = Settings(
        database_url="postgresql://unused",
        jwks_url="http://unused",
        answer_tier="high",
        helper_tier="medium",
    )
    provider = FakeProvider([AIMessage("a"), AIMessage("b"), AIMessage("c")])
    agents = build_agents(provider, tools, settings)

    await agents["weather"].run("forecast")
    await agents["search"].run("reviews")
    await agents["fetch"].run("read")

    assert provider.bound_tiers == ["high", "medium", "medium"]


def test_the_helper_tier_defaults_to_low_and_an_empty_value_means_the_default() -> None:
    assert make_settings().helper_tier == "low"
    blank = Settings.model_validate(
        {"database_url": "postgresql://unused", "jwks_url": "http://unused", "helper_tier": " "}
    )
    assert blank.helper_tier == "low"


async def test_calls_past_the_cap_are_not_run_and_get_a_limit_reached_error() -> None:
    searched: list[str] = []
    calls = [ToolCall(name="search__web", args={"query": f"q{i}"}, id=f"c{i}") for i in range(3)]
    provider = FakeProvider([AIMessage(content="", tool_calls=calls), AIMessage("Done.")])
    tools = Tools(domains={"search": [search_tool(searched)]})
    agent = build_agents(provider, tools, make_settings())["search"]

    result = await agent.run("reviews of Hotel Gracery Tokyo")

    assert searched == ["q0", "q1"]
    assert result.calls == [("search__web", "[]", True), ("search__web", "[]", True)]
    refusal = provider.seen[1][-1]
    assert isinstance(refusal, ToolMessage)
    assert refusal.status == "error"
    assert refusal.content == "limit reached"


async def test_a_guard_can_rewrite_a_call_or_refuse_it_without_running_the_tool() -> None:
    read: list[str] = []

    def guard(call: ToolCall) -> ToolCall | str:
        url = str(call["args"]["url"])
        if "evil" in url:
            return "refused"
        checked = copy.copy(call)
        checked["args"] = {"url": unescape(url)}
        return checked

    calls = [
        ToolCall(name="fetch__page", args={"url": "https://a.example/?x=1&amp;y=2"}, id="c1"),
        ToolCall(name="fetch__page", args={"url": "https://evil.example/"}, id="c2"),
    ]
    provider = FakeProvider([AIMessage(content="", tool_calls=calls), AIMessage("Done.")])
    tools = Tools(domains={"fetch": [fetch_tool(read)]})
    agent = build_agents(provider, tools, make_settings())["fetch"]

    result = await agent.run("read these", guard)

    assert read == ["https://a.example/?x=1&y=2"]
    assert result.calls == [("fetch__page", "{}", True)]
    refusal = provider.seen[1][-1]
    assert isinstance(refusal, ToolMessage)
    assert (refusal.status, refusal.content) == ("error", "refused")


async def test_the_cap_counts_per_run_not_per_agent() -> None:
    searched: list[str] = []
    provider = FakeProvider(
        [
            tool_reply("search__web", {"query": "one"}),
            AIMessage("First."),
            tool_reply("search__web", {"query": "two"}),
            AIMessage("Second."),
        ]
    )
    tools = Tools(domains={"search": [search_tool(searched)]})
    agent = build_agents(provider, tools, make_settings())["search"]

    await agent.run("first")
    await agent.run("second")

    assert searched == ["one", "two"]


async def test_an_agent_without_a_cap_runs_every_call() -> None:
    executed: list[str] = []
    calls = [
        ToolCall(name="weather__forecast", args={"city": f"City{i}"}, id=f"c{i}") for i in range(4)
    ]
    provider = FakeProvider([AIMessage(content="", tool_calls=calls), AIMessage("Done.")])
    agent = build_agents(provider, toolbox(executed), make_settings())["weather"]

    await agent.run("tour")

    assert len(executed) == 4


def export_stub(executed: list[str]) -> BaseTool:
    async def export(format: str) -> str:
        executed.append(format)
        return f"File ready: plan.{format}."

    return StructuredTool.from_function(
        coroutine=export, name="artifact__export", description="Export."
    )


def test_the_artifact_spec_runs_on_the_helper_tier_with_one_call() -> None:
    spec = next(spec for spec in SPECS if spec.domain == "artifact")

    assert (spec.name, spec.tier, spec.max_calls) == ("artifact_agent", "helper", 1)
    assert [tool.name for tool in spec.tools] == ["artifact__export"]


async def test_with_tools_binds_the_extra_tools_to_the_copy_only() -> None:
    executed: list[str] = []
    provider = FakeProvider([tool_reply("artifact__export", {"format": "csv"}), AIMessage("Done.")])
    agent = build_agents(provider, Tools(), make_settings())["artifact"]

    bound = agent.with_tools([export_stub(executed)])
    original = await agent.run("csv please")
    result = await bound.run("csv please")

    assert original.summary == NO_TOOLS
    assert executed == ["csv"]
    assert result.calls == [("artifact__export", "File ready: plan.csv.", True)]
    assert provider.binds == [(["artifact__export"], False)] * 2
    assert bound.spec is agent.spec


async def test_with_tools_still_filters_the_extra_tools_by_the_spec() -> None:
    executed: list[str] = []
    provider = FakeProvider([AIMessage("Nothing.")])
    agent = build_agents(provider, Tools(), make_settings())["artifact"]

    await agent.with_tools([export_stub(executed), forecast_tool(executed)]).run("csv")

    assert provider.binds == [(["artifact__export"], False)]


async def test_the_artifact_agent_runs_one_export_per_run() -> None:
    executed: list[str] = []
    calls = [
        ToolCall(name="artifact__export", args={"format": "csv"}, id="c1"),
        ToolCall(name="artifact__export", args={"format": "docx"}, id="c2"),
    ]
    provider = FakeProvider([AIMessage(content="", tool_calls=calls), AIMessage("Done.")])
    agent = build_agents(provider, Tools(), make_settings())["artifact"]

    result = await agent.with_tools([export_stub(executed)]).run("both")

    assert executed == ["csv"]
    assert len(result.calls) == 1
    refusal = provider.seen[1][-1]
    assert isinstance(refusal, ToolMessage)
    assert (refusal.status, refusal.content) == ("error", "limit reached")


def test_without_a_provider_there_are_no_agents() -> None:
    assert build_agents(None, Tools(), make_settings()) == {}
