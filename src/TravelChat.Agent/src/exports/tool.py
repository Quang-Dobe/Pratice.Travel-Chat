import asyncio
import logging
from typing import TYPE_CHECKING, TypedDict
from uuid import UUID

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langgraph.config import get_stream_writer

from src.exports import repository
from src.exports.render import file_name, is_draft, parse_format, render
from src.graph.state import PlanCard
from src.utils.tools import with_events
from src.utils.trip import Brief

if TYPE_CHECKING:
    from src.main import DictRowPool

logger = logging.getLogger(__name__)

EXPORT_TOOL = "artifact__export"
NO_CARD = "There is no plan card to export yet."
UNKNOWN_FORMAT = "Unknown format. Use html, docx, excel (xlsx) or csv."
NOT_SAVED = "The file could not be saved right now."
NOT_MADE = "The file could not be created."


class ExportItem(TypedDict):
    id: str
    format: str
    fileName: str
    url: str
    draft: bool


def download_url(conversation_id: str, export_id: UUID) -> str:
    return f"/api/conversations/{conversation_id}/exports/{export_id}"


def export_tool(
    card: PlanCard | None,
    brief: Brief,
    conversation_id: str,
    pool: DictRowPool | None,
    made: list[ExportItem],
) -> BaseTool:
    async def export(format: str) -> str:
        fmt = parse_format(format)
        if fmt is None:
            raise ToolException(UNKNOWN_FORMAT)
        if card is None:
            return NO_CARD
        if pool is None:
            raise ToolException(NOT_SAVED)
        try:
            data = await asyncio.to_thread(render, card, brief, fmt)
        except Exception:
            logger.exception("Rendering the %s export failed", fmt)
            raise ToolException(NOT_MADE) from None
        name = file_name(card, brief, fmt)
        draft = is_draft(card)
        try:
            export_id = await repository.insert(
                pool, UUID(conversation_id), card["id"], fmt, name, data, draft
            )
        except Exception:
            logger.exception("Saving the %s export failed", fmt)
            raise ToolException(NOT_SAVED) from None
        item = ExportItem(
            id=str(export_id),
            format=fmt,
            fileName=name,
            url=download_url(conversation_id, export_id),
            draft=draft,
        )
        made.append(item)
        get_stream_writer()({"event": "artifact", **item})
        return f"File ready: {name}."

    return with_events(
        StructuredTool.from_function(
            coroutine=export,
            name=EXPORT_TOOL,
            description=(
                "Create the file of the latest plan card. format is one of html, docx, "
                "xlsx (Excel) or csv."
            ),
        )
    )
