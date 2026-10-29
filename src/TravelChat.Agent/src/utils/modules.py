import importlib
from types import ModuleType

PLAN_GRAPH_PACKAGE = "src.graph.plan-graph"


def load(name: str) -> ModuleType:
    return importlib.import_module(f"{PLAN_GRAPH_PACKAGE}.{name}")
