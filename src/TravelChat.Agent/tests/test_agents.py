from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel

from src.agents.base import MAX_ROUNDS, NO_TOOLS
from src.agents.registry import SPECS, build_agents
from src.settings import Settings, Tier
from src.utils.tools import Tools


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
        raise RuntimeError("the fake chat is only used through with_tools")


class FakeProvider:
    def __init__(self, script: list[AIMessage]) -> None:
        self.script = script
        self.binds: list[tuple[list[str], bool]] = []
        self.seen: list[list[BaseMessage]] = []

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
        self.binds.append(([tool.name for tool in tools], final))

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

    assert set(agents) == {"flights", "hotels", "weather", "places"}
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


def test_without_a_provider_there_are_no_agents() -> None:
    assert build_agents(None, Tools(), make_settings()) == {}
