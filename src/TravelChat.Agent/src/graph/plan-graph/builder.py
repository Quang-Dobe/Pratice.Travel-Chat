from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.graph.deps import PlannerDeps
from src.graph.state import PlannerState
from src.utils.modules import load

EXTRACT = "extract"
NEXT_STEP = "next_step"
ASK_MISSING = "ask_missing"
ASK_BUDGET = "ask_budget"
SEARCH_FLIGHTS = "search_flights"
SEARCH_HOTELS = "search_hotels"
DRAFT_ITINERARY = "draft_itinerary"
PLAN_CARD = "plan_card"
RECORD_APPROVAL = "record_approval"
ASK_CHANGE = "ask_change"
WRAP_UP = "wrap_up"

NODE_NAMES = (
    EXTRACT,
    NEXT_STEP,
    ASK_MISSING,
    ASK_BUDGET,
    SEARCH_FLIGHTS,
    SEARCH_HOTELS,
    DRAFT_ITINERARY,
    PLAN_CARD,
    RECORD_APPROVAL,
    ASK_CHANGE,
    WRAP_UP,
)
ENDING_NODES = (ASK_MISSING, ASK_BUDGET, ASK_CHANGE, PLAN_CARD, WRAP_UP)


def build(deps: PlannerDeps) -> CompiledStateGraph[PlannerState, None, PlannerState, PlannerState]:
    modules = {name: load(name) for name in NODE_NAMES}
    steps = modules[NEXT_STEP]

    def route(state: PlannerState) -> str:
        return str(steps.route(state))

    def after_flights(state: PlannerState) -> str:
        return str(steps.after(state, "flights"))

    def after_hotels(state: PlannerState) -> str:
        return str(steps.after(state, "hotels"))

    def after_approval(state: PlannerState) -> str:
        return PLAN_CARD if state.get("note") else WRAP_UP

    async def extract_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[EXTRACT].run(state, deps)
        return update

    async def next_step_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[NEXT_STEP].run(state, deps)
        return update

    async def ask_missing_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[ASK_MISSING].run(state, deps)
        return update

    async def ask_budget_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[ASK_BUDGET].run(state, deps)
        return update

    async def search_flights_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[SEARCH_FLIGHTS].run(state, deps)
        return update

    async def search_hotels_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[SEARCH_HOTELS].run(state, deps)
        return update

    async def draft_itinerary_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[DRAFT_ITINERARY].run(state, deps)
        return update

    async def plan_card_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[PLAN_CARD].run(state, deps)
        return update

    async def record_approval_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[RECORD_APPROVAL].run(state, deps)
        return update

    async def ask_change_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[ASK_CHANGE].run(state, deps)
        return update

    async def wrap_up_node(state: PlannerState) -> dict[str, Any]:
        update: dict[str, Any] = await modules[WRAP_UP].run(state, deps)
        return update

    graph = StateGraph(PlannerState)
    graph.add_node(EXTRACT, extract_node)
    graph.add_node(NEXT_STEP, next_step_node)
    graph.add_node(ASK_MISSING, ask_missing_node)
    graph.add_node(ASK_BUDGET, ask_budget_node)
    graph.add_node(SEARCH_FLIGHTS, search_flights_node)
    graph.add_node(SEARCH_HOTELS, search_hotels_node)
    graph.add_node(DRAFT_ITINERARY, draft_itinerary_node)
    graph.add_node(PLAN_CARD, plan_card_node)
    graph.add_node(RECORD_APPROVAL, record_approval_node)
    graph.add_node(ASK_CHANGE, ask_change_node)
    graph.add_node(WRAP_UP, wrap_up_node)
    graph.add_edge(START, EXTRACT)
    graph.add_edge(EXTRACT, NEXT_STEP)
    graph.add_conditional_edges(
        NEXT_STEP,
        route,
        [
            RECORD_APPROVAL,
            ASK_CHANGE,
            ASK_MISSING,
            ASK_BUDGET,
            SEARCH_FLIGHTS,
            SEARCH_HOTELS,
            DRAFT_ITINERARY,
            PLAN_CARD,
        ],
    )
    graph.add_conditional_edges(
        SEARCH_FLIGHTS, after_flights, [SEARCH_HOTELS, DRAFT_ITINERARY, PLAN_CARD]
    )
    graph.add_conditional_edges(SEARCH_HOTELS, after_hotels, [DRAFT_ITINERARY, PLAN_CARD])
    graph.add_edge(DRAFT_ITINERARY, PLAN_CARD)
    graph.add_conditional_edges(RECORD_APPROVAL, after_approval, [WRAP_UP, PLAN_CARD])
    for name in ENDING_NODES:
        graph.add_edge(name, END)
    return graph.compile()
