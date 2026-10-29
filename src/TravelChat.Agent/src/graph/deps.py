from dataclasses import dataclass, field

from src.agents.base import DomainAgent
from src.providers.base import ChatProvider
from src.settings import Settings
from src.utils.tools import Tools


@dataclass(frozen=True)
class PlannerDeps:
    provider: ChatProvider | None
    settings: Settings
    tools: Tools = field(default_factory=Tools)
    agents: dict[str, DomainAgent] = field(default_factory=dict)

    def chat(self) -> ChatProvider:
        if self.provider is None:
            raise RuntimeError("The planner graph ran without a configured model provider.")
        return self.provider
