import ast
import logging
import math
import operator
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, TypedDict

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.config import get_stream_writer

from src.settings import Settings

logger = logging.getLogger(__name__)

MAX_EXPRESSION_LENGTH = 200
NOTICE_MARKERS = ("no key set", "free-tier limit reached")
DOMAINS = ("flights", "hotels", "weather", "places", "geo", "search", "fetch")
DOMAIN_SEPARATOR = "__"

TOOL_KINDS: dict[str, str] = {"calculator": "calculate", "time": "calculate"}
RETRIEVE_KIND = "retrieve"

BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

type McpToolLoader = Callable[[Settings], Awaitable[list[BaseTool]]]
type RawCall = tuple[str, str, bool]


class ToolUse(TypedDict):
    name: str
    kind: str


def tool_kind(name: str) -> str:
    return TOOL_KINDS.get(name, RETRIEVE_KIND)


def tool_use(name: str) -> ToolUse:
    return ToolUse(name=name, kind=tool_kind(name))


def tool_uses(calls: Sequence[RawCall]) -> list[ToolUse]:
    return [tool_use(name) for name, _, _ in calls]


def _evaluate_node(node: ast.AST) -> float:
    match node:
        case ast.Expression(body=body):
            return _evaluate_node(body)
        case ast.Constant(value=int() | float() as value) if not isinstance(value, bool):
            return float(value)
        case ast.BinOp(left=left, op=op, right=right) if type(op) in BINARY_OPERATORS:
            return BINARY_OPERATORS[type(op)](_evaluate_node(left), _evaluate_node(right))
        case ast.UnaryOp(op=op, operand=operand) if type(op) in UNARY_OPERATORS:
            return UNARY_OPERATORS[type(op)](_evaluate_node(operand))
        case _:
            raise ToolException("Unsupported expression: use numbers, + - * / and parentheses.")


def evaluate_expression(expression: str) -> str:
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ToolException("Expression is too long.")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _evaluate_node(tree)
    except ZeroDivisionError as error:
        raise ToolException("Division by zero.") from error
    except (SyntaxError, RecursionError, ValueError, OverflowError) as error:
        raise ToolException("Invalid expression.") from error
    if not math.isfinite(result):
        raise ToolException("Result is out of range.")
    return f"{result:.12g}"


def _iso_date(value: str | None, label: str) -> date:
    if not value:
        raise ToolException(f"This operation needs {label} as an ISO date (YYYY-MM-DD).")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ToolException("Dates must be ISO format (YYYY-MM-DD).") from error


def time_operation(
    op: Literal["now", "compare", "days_between"], a: str | None = None, b: str | None = None
) -> str:
    if op == "now":
        return datetime.now(UTC).isoformat(timespec="seconds")
    first = _iso_date(a, "a")
    second = _iso_date(b, "b")
    days = (second - first).days
    if op == "days_between":
        return str(days)
    if days > 0:
        return f"a is {days} days before b"
    if days < 0:
        return f"a is {-days} days after b"
    return "a and b are the same day"


calculator = StructuredTool.from_function(
    func=evaluate_expression,
    name="calculator",
    description=(
        "Evaluate an arithmetic expression with numbers, + - * / and parentheses. "
        "Example: (120 * 3 + 45) / 2."
    ),
)

time_tool = StructuredTool.from_function(
    func=time_operation,
    name="time",
    description=(
        "op=now returns the current UTC date and time. "
        "op=compare says which of the ISO dates a and b comes first. "
        "op=days_between returns the number of days from date a to date b (ISO dates)."
    ),
)


def _text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = [
            item if isinstance(item, str) else str(item.get("text", ""))
            for item in result
            if isinstance(item, str | dict)
        ]
        return "\n".join(part for part in parts if part)
    return str(result)


def _notice_for(text: str) -> str | None:
    lowered = text.lower()
    return text if any(marker in lowered for marker in NOTICE_MARKERS) else None


def with_events(inner: BaseTool) -> BaseTool:
    kind = tool_kind(inner.name)

    async def run(**arguments: Any) -> str:
        writer = get_stream_writer()
        writer({"event": "tool_call", "kind": kind, "name": inner.name, "status": "started"})
        failure: ToolException | None = None
        text = ""
        try:
            text = _text(await inner.ainvoke(arguments))
        except ToolException as error:
            failure = error
        finally:
            writer({"event": "tool_call", "kind": kind, "name": inner.name, "status": "finished"})
        if failure is not None:
            notice = _notice_for(str(failure))
            if notice is not None:
                writer({"event": "notice", "message": notice})
            raise failure
        return text

    return StructuredTool.from_function(
        coroutine=run,
        name=inner.name,
        description=inner.description,
        args_schema=inner.args_schema,
        handle_tool_error=True,
    )


@dataclass(frozen=True)
class Tools:
    local: list[BaseTool] = field(default_factory=list)
    domains: dict[str, list[BaseTool]] = field(default_factory=dict)

    def local_named(self, name: str) -> BaseTool | None:
        return next((tool for tool in self.local if tool.name == name), None)

    def for_domain(self, domain: str) -> list[BaseTool]:
        return self.domains.get(domain, [])


async def fetch_mcp_tools(settings: Settings) -> list[BaseTool]:
    client = MultiServerMCPClient(
        {"q-mcp": {"transport": "streamable_http", "url": settings.mcp_url}},
        handle_tool_errors=False,
    )
    return await client.get_tools()


def _group(remote: Sequence[BaseTool]) -> dict[str, list[BaseTool]]:
    groups: dict[str, list[BaseTool]] = {domain: [] for domain in DOMAINS}
    for tool in sorted(remote, key=lambda candidate: candidate.name):
        domain, separator, _ = tool.name.partition(DOMAIN_SEPARATOR)
        if separator:
            groups.setdefault(domain, []).append(with_events(tool))
    return groups


async def load_tools(settings: Settings, mcp_loader: McpToolLoader = fetch_mcp_tools) -> Tools:
    remote: list[BaseTool] = []
    try:
        remote = await mcp_loader(settings)
    except Exception:
        logger.exception("q-mcp unreachable at %s; no data tools are available", settings.mcp_url)
    return Tools(local=[with_events(calculator), with_events(time_tool)], domains=_group(remote))
