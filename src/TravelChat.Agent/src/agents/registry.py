from src.agents import artifact, fetch, flights, hotels, places, search, weather
from src.agents.base import AgentSpec, DomainAgent
from src.providers.base import ChatProvider
from src.settings import Settings
from src.utils.tools import Tools

SPECS: tuple[AgentSpec, ...] = (
    flights.SPEC,
    hotels.SPEC,
    weather.SPEC,
    places.SPEC,
    search.SPEC,
    fetch.SPEC,
    artifact.SPEC,
)


def build_agents(
    provider: ChatProvider | None, tools: Tools, settings: Settings
) -> dict[str, DomainAgent]:
    if provider is None:
        return {}
    models = {
        "answer": provider.model(settings.answer_tier),
        "helper": provider.model(settings.helper_tier),
    }
    return {
        spec.domain: DomainAgent(spec, provider, models[spec.tier], tools.for_domain(spec.domain))
        for spec in SPECS
    }
