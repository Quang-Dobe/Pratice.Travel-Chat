from typing import cast

from langgraph.graph.state import CompiledStateGraph

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.utils.modules import load

type PlanGraph = CompiledStateGraph[PlannerState, None, PlannerState, PlannerState]


def build_plan(deps: PlannerDeps) -> PlanGraph:
    return cast("PlanGraph", load("builder").build(deps))
