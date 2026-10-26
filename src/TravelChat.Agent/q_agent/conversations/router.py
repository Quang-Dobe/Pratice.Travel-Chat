from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel

from q_agent.auth import CurrentUserDep
from q_agent.conversations import repository
from q_agent.conversations.graph import ChatGraph, thread_config
from q_agent.conversations.repository import ConversationRow
from q_agent.conversations.title import make_title

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class TextRequest(ApiModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ConversationItem(ApiModel):
    id: UUID
    title: str
    created_at: datetime


class ConversationList(ApiModel):
    items: list[ConversationItem]


class MessageItem(ApiModel):
    role: Literal["user", "assistant"]
    text: str


class ConversationDetail(ConversationItem):
    messages: list[MessageItem]


def _to_item(row: ConversationRow) -> ConversationItem:
    return ConversationItem(id=row.id, title=row.title, created_at=row.created_at)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


async def get_owned_conversation(
    conversation_id: str, request: Request, user: CurrentUserDep
) -> ConversationRow:
    try:
        parsed_id = UUID(conversation_id)
    except ValueError:
        raise _not_found() from None
    row = await repository.get_for_user(request.app.state.pool, parsed_id, user.uid)
    if row is None:
        raise _not_found()
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
    messages: list[MessageItem] = []
    for message in snapshot.values.get("messages", []):
        if isinstance(message, HumanMessage):
            messages.append(MessageItem(role="user", text=message.text))
        elif isinstance(message, AIMessage):
            messages.append(MessageItem(role="assistant", text=message.text))
    item = _to_item(conversation)
    return ConversationDetail(**item.model_dump(), messages=messages)


@router.post("/{conversation_id}/messages", response_class=EventSourceResponse)
async def send_message(
    body: TextRequest, conversation: OwnedConversation, request: Request, user: CurrentUserDep
) -> AsyncIterator[ServerSentEvent]:
    graph: ChatGraph = request.app.state.graph
    stream: AsyncIterator[Any] = graph.astream(
        {"messages": [HumanMessage(body.text)]},
        thread_config(user.uid, conversation.id),
        stream_mode="custom",
        version="v2",
    )
    async for part in stream:
        yield ServerSentEvent(event="token", data={"text": part["data"]["text"]})
    yield ServerSentEvent(event="done", data={})
