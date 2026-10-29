import logging
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Annotated, Any, Literal, Self, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.types import StreamMode
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from src.auth import CurrentUser, CurrentUserDep
from src.conversations import repository
from src.conversations.repository import ConversationRow
from src.conversations.title import make_title
from src.graph.builder import ChatGraph, thread_config
from src.graph.deps import PlannerDeps
from src.graph.state import Pill, PlanCard, PlannerState, card_payload
from src.utils.messages import ACTION, TURN_TOOLS
from src.utils.tools import tool_kind

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])

STREAM_MODES: list[StreamMode] = ["messages", "custom"]
TOKEN_NODES = frozenset({"answer", "ask_missing", "ask_change"})
PARAGRAPH_BREAK = "\n\n"
FORWARDED_EVENTS = frozenset({"tool_call", "plan_card", "pills", "notice"})
NOT_CONFIGURED = "The AI model is not configured."
UNAVAILABLE = "The AI model is unavailable right now."


def _notice(message: str) -> ServerSentEvent:
    return ServerSentEvent(event="notice", data={"message": message})


def _done() -> ServerSentEvent:
    return ServerSentEvent(event="done", data={})


def _token(text: str) -> ServerSentEvent:
    return ServerSentEvent(event="token", data={"text": text})


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class TextRequest(ApiModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ActionRequest(ApiModel):
    type: Literal["approve", "reject"]
    card_id: str | None = None
    flight_id: str | None = None
    hotel_id: str | None = None

    @model_validator(mode="after")
    def approve_needs_picks(self) -> Self:
        if self.type == "approve" and not (self.card_id and self.flight_id and self.hotel_id):
            raise ValueError("approve needs cardId, flightId and hotelId")
        return self


class SendRequest(TextRequest):
    action: ActionRequest | None = None


class ConversationItem(ApiModel):
    id: UUID
    title: str
    created_at: datetime


class ConversationList(ApiModel):
    items: list[ConversationItem]


class ToolCallItem(ApiModel):
    name: str
    kind: str


class OptionItem(ApiModel):
    id: str
    summary: str
    price_usd: float
    deep_link: str


class PickedItem(ApiModel):
    flight_id: str
    hotel_id: str


class DayItem(ApiModel):
    date: str
    activities: list[str]
    weather: str
    cost_usd: float


class BudgetItem(ApiModel):
    daily_usd: float
    total_usd: float
    budget_usd: float
    over_budget: bool


class PlanCardItem(ApiModel):
    id: str
    status: Literal["open", "approved", "replaced"]
    flights: list[OptionItem]
    hotels: list[OptionItem]
    picked: PickedItem
    days: list[DayItem]
    budget: BudgetItem


class PillItem(ApiModel):
    text: str
    action: str | None


class MessageItem(ApiModel):
    role: Literal["user", "assistant"]
    text: str
    tool_calls: list[ToolCallItem] = []
    plan_card: PlanCardItem | None = None
    pills: list[PillItem] = []


class ConversationDetail(ConversationItem):
    messages: list[MessageItem]


def _to_item(row: ConversationRow) -> ConversationItem:
    return ConversationItem(id=row.id, title=row.title, created_at=row.created_at)


def _tool_calls(message: AIMessage) -> list[ToolCallItem]:
    recorded = message.additional_kwargs.get(TURN_TOOLS) or []
    names = [str(call["name"]) for call in recorded]
    return [ToolCallItem(name=name, kind=tool_kind(name)) for name in names]


def _build_messages(
    messages: Sequence[BaseMessage], cards: Sequence[PlanCard], pills: Sequence[Pill]
) -> list[MessageItem]:
    by_message = {card["messageId"]: card for card in cards}
    items: list[MessageItem] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            items.append(MessageItem(role="user", text=message.text))
        elif isinstance(message, AIMessage) and message.text:
            card = by_message.get(message.id or "")
            plan_card = PlanCardItem.model_validate(card_payload(card)) if card else None
            items.append(
                MessageItem(
                    role="assistant",
                    text=message.text,
                    tool_calls=_tool_calls(message),
                    plan_card=plan_card,
                )
            )
    for index in range(len(items) - 1, -1, -1):
        if items[index].role == "assistant":
            attached = [PillItem.model_validate(pill) for pill in pills]
            items[index] = items[index].model_copy(update={"pills": attached})
            break
    return items


async def _stream_turn(
    request: Request,
    conversation: ConversationRow,
    user: CurrentUser,
    provider_errors: type[Exception],
    turn_input: PlannerState,
) -> AsyncIterator[ServerSentEvent]:
    graph: ChatGraph = request.app.state.graph
    config = thread_config(user.uid, conversation.id)
    stream: AsyncIterator[Any] = graph.astream(
        turn_input, config, stream_mode=STREAM_MODES, version="v2", subgraphs=True
    )
    last_chunk: Any = None
    wrote_text = False
    try:
        async for part in stream:
            if part["type"] == "messages":
                chunk, metadata = part["data"]
                if metadata.get("langgraph_node") in TOKEN_NODES and chunk.text:
                    if chunk.id != last_chunk:
                        last_chunk = chunk.id
                        if wrote_text:
                            yield _token(PARAGRAPH_BREAK)
                    wrote_text = True
                    yield _token(str(chunk.text))
            elif part["type"] == "custom":
                data: dict[str, Any] = part["data"]
                name = data.get("event")
                if name == "title":
                    await repository.set_title(
                        request.app.state.pool, conversation.id, user.uid, data["title"]
                    )
                elif name == "token":
                    wrote_text = True
                    yield _token(str(data["text"]))
                elif name in FORWARDED_EVENTS:
                    yield ServerSentEvent(
                        event=name,
                        data={key: value for key, value in data.items() if key != "event"},
                    )
    except provider_errors, ConnectionError:
        logger.exception("Model call failed during a chat turn")
        yield _notice(UNAVAILABLE)
    except Exception:
        logger.exception("Chat turn failed")
        yield _notice(UNAVAILABLE)
    yield _done()


async def get_owned_conversation(
    conversation_id: str, request: Request, user: CurrentUserDep
) -> ConversationRow:
    try:
        parsed_id = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found") from None
    row = await repository.get_for_user(request.app.state.pool, parsed_id, user.uid)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return row


OwnedConversation = Annotated[ConversationRow, Depends(get_owned_conversation)]


@router.get("")
async def list_conversations(request: Request, user: CurrentUserDep) -> ConversationList:
    rows = await repository.list_for_user(request.app.state.pool, user.uid)
    return ConversationList(items=[_to_item(row) for row in rows])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: TextRequest, request: Request, user: CurrentUserDep
) -> ConversationItem:
    row = await repository.create(request.app.state.pool, user.uid, make_title(body.text))
    return _to_item(row)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation: OwnedConversation, request: Request, user: CurrentUserDep
) -> ConversationDetail:
    graph: ChatGraph = request.app.state.graph
    snapshot = await graph.aget_state(thread_config(user.uid, conversation.id))
    messages = _build_messages(
        snapshot.values.get("messages", []),
        snapshot.values.get("cards") or [],
        snapshot.values.get("pills") or [],
    )
    return ConversationDetail(**_to_item(conversation).model_dump(), messages=messages)


@router.post("/{conversation_id}/messages", response_class=EventSourceResponse)
async def send_message(
    body: SendRequest, conversation: OwnedConversation, request: Request, user: CurrentUserDep
) -> AsyncIterator[ServerSentEvent]:
    deps: PlannerDeps = request.app.state.planner
    if deps.provider is None:
        yield _notice(NOT_CONFIGURED)
        yield _done()
        return
    kwargs: dict[str, Any] = {}
    if body.action is not None:
        kwargs[ACTION] = body.action.model_dump(by_alias=True, exclude_none=True)
    message = HumanMessage(body.text, additional_kwargs=kwargs)
    turn_input = cast("PlannerState", {"messages": [message]})
    errors = deps.provider.errors
    async for event in _stream_turn(request, conversation, user, errors, turn_input):
        yield event
