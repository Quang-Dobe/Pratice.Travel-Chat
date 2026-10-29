from src.agents import flights, hotels, places, weather
from src.agents.base import AgentSpec, DomainAgent
from src.providers.base import ChatProvider
from src.settings import Settings
from src.utils.tools import Tools

SPECS: tuple[AgentSpec, ...] = (flights.SPEC, hotels.SPEC, weather.SPEC, places.SPEC)


def build_agents(
    provider: ChatProvider | None, tools: Tools, settings: Settings
) -> dict[str, DomainAgent]:
    if provider is None:
        return {}
    model = provider.model(settings.answer_tier)
    return {
        spec.domain: DomainAgent(spec, provider, model, tools.for_domain(spec.domain))
        for spec in SPECS
    }
