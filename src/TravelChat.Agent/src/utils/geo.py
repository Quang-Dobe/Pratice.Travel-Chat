import logging
from collections.abc import Sequence

from langchain_core.tools import BaseTool

from src.utils.messages import parse_json, parse_location
from src.utils.tools import Tools
from src.utils.trip import Location

logger = logging.getLogger(__name__)

GEOCODE_TOOL = "geo__geocode"
GEO_DOMAIN = "geo"
MAX_PLACES = 30


def unique_names(names: Sequence[str], limit: int = MAX_PLACES) -> list[str]:
    return list(dict.fromkeys(name for name in names if name.strip()))[:limit]


def _located(data: object) -> dict[str, Location]:
    if isinstance(data, dict):
        data = data.get("data")
    if not isinstance(data, list):
        return {}
    found: dict[str, Location] = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        location = parse_location(item)
        if location is not None:
            found[item["name"]] = location
    return found


def geocode_tool(tools: Tools) -> BaseTool | None:
    return next((tool for tool in tools.for_domain(GEO_DOMAIN) if tool.name == GEOCODE_TOOL), None)


async def geocode(tools: Tools, names: Sequence[str]) -> dict[str, Location]:
    places = unique_names(names)
    tool = geocode_tool(tools)
    if not places or tool is None:
        return {}
    try:
        text = str(await tool.ainvoke({"places": places}))
    except Exception:
        logger.exception("Geocoding failed; the card goes out without those pins")
        return {}
    return _located(parse_json(text))
