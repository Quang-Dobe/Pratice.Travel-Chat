from collections.abc import Sequence
from datetime import date

from src.starters.repository import SummaryRow

MAX_DESTINATIONS = 3
MAX_STARTERS = 4
TRAVEL_NOUNS = frozenset(
    {"trip", "tour", "getaway", "holiday", "vacation", "break", "cruise", "trek"}
)
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _trip_phrase(trip_type: str | None) -> str:
    kind = _clean(trip_type)
    last_word = kind.split()[-1].casefold() if kind else ""
    if last_word in TRAVEL_NOUNS:
        return kind
    return f"{kind} trip" if kind else "trip"


def _month(start_date: date | None) -> str:
    return MONTHS[start_date.month - 1] if start_date else ""


def _join(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _plan_another(row: SummaryRow) -> str:
    month = _month(row.start_date)
    parts = ["Plan another", _trip_phrase(row.trip_type), "to", _clean(row.destination)]
    if month:
        parts.extend(["in", month])
    return " ".join(parts)


def _find_like(row: SummaryRow) -> str:
    preferences = [text for text in (_clean(item) for item in row.preferences) if text]
    text = f"Find a trip like {_clean(row.destination)}"
    return f"{text} for {_join(preferences)}" if preferences else text


def starter_texts(rows: Sequence[SummaryRow]) -> list[str]:
    usable = [row for row in rows if _clean(row.destination)]
    seen_destinations: set[str] = set()
    candidates: list[str] = []
    for row in usable:
        key = _clean(row.destination).casefold()
        if key in seen_destinations:
            continue
        seen_destinations.add(key)
        candidates.append(_plan_another(row))
        if len(seen_destinations) == MAX_DESTINATIONS:
            break
    if rows and _clean(rows[0].destination):
        candidates.append(_find_like(rows[0]))
    texts: list[str] = []
    for text in candidates:
        if text not in texts:
            texts.append(text)
    return texts[:MAX_STARTERS]
