import copy
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, TypedDict, cast

type Level = Literal["low", "mid", "high"]
type Part = Literal["flights", "hotels", "itinerary"]
type Change = Literal["flights", "hotel"]
type Focus = Literal["hotels", "flights", "places"]
type ChangedField = Literal["destination", "dates", "travellers", "origin", "interests", "budget"]

DEFAULT_TRAVELLERS = 1
DEFAULT_LEVEL: Level = "mid"
DEFAULT_START_OFFSET_DAYS = 14
MAX_TRAVELLERS = 9
MAX_ITINERARY_DAYS = 30
FORECAST_RANGE_DAYS = 15
NO_FORECAST = "no forecast available yet"

DAILY_COST_USD: dict[str, int] = {"low": 40, "mid": 90, "high": 180}

PART_ORDER: tuple[Part, ...] = ("flights", "hotels", "itinerary")
_ALL_PARTS: frozenset[Part] = frozenset(PART_ORDER)
_REDO: dict[ChangedField, frozenset[Part]] = {
    "dates": _ALL_PARTS,
    "destination": _ALL_PARTS,
    "travellers": _ALL_PARTS,
    "origin": frozenset({"flights"}),
    "interests": frozenset({"itinerary"}),
    "budget": frozenset(),
}
_REDO_FOR_CHANGE: dict[Change, frozenset[Part]] = {
    "flights": frozenset({"flights", "itinerary"}),
    "hotel": frozenset({"hotels"}),
}
_REDO_FOR_FOCUS: dict[Focus, frozenset[Part]] = {
    "hotels": frozenset({"hotels"}),
    "flights": frozenset({"flights", "itinerary"}),
    "places": frozenset({"itinerary"}),
}


class Brief(TypedDict, total=False):
    destination: str
    origin: str
    start_date: str
    end_date: str
    travellers: int
    budget_level: Level
    budget_usd: float
    interests: list[str]


class Criteria(TypedDict, total=False):
    flights: str
    hotels: str


class Option(TypedDict):
    id: str
    summary: str
    priceUsd: float
    deepLink: str


class Day(TypedDict):
    date: str
    activities: list[str]
    weather: str
    costUsd: float


class Budget(TypedDict):
    flightUsd: float
    hotelUsd: float
    dailyUsd: float
    totalUsd: float
    budgetUsd: float | None
    overBudget: bool


class Trip(TypedDict, total=False):
    brief: Brief
    assumed: list[str]
    criteria: Criteria
    flights: list[Option]
    hotels: list[Option]
    days: list[Day]
    redo: list[Part]


def today() -> date:
    return datetime.now(UTC).date()


def redo_for(changed: set[ChangedField], change: Change | None) -> list[Part]:
    parts: set[Part] = set()
    for field in changed:
        parts |= _REDO[field]
    if change is not None:
        parts |= _REDO_FOR_CHANGE[change]
    return [part for part in PART_ORDER if part in parts]


def redo_for_focus(focus: Focus) -> list[Part]:
    return [part for part in PART_ORDER if part in _REDO_FOR_FOCUS[focus]]


def merge_parts(first: Sequence[Part], second: Sequence[Part]) -> list[Part]:
    joined = {*first, *second}
    return [part for part in PART_ORDER if part in joined]


def missing_fields(brief: Brief) -> list[str]:
    missing: list[str] = []
    if not brief.get("destination"):
        missing.append("destination")
    if not brief.get("origin"):
        missing.append("origin")
    if trip_days(brief) < 1:
        missing.append("dates or trip length")
    return missing


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _positive_int(value: object, maximum: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value if maximum is None else min(value, maximum)


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return float(value)


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def trip_days(brief: Brief) -> int:
    start = _parse_date(brief.get("start_date"))
    end = _parse_date(brief.get("end_date"))
    if start is None or end is None or end < start:
        return 0
    return (end - start).days + 1


def trip_dates(brief: Brief) -> list[str]:
    start = _parse_date(brief.get("start_date"))
    if start is None:
        return []
    count = min(trip_days(brief), MAX_ITINERARY_DAYS)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(count)]


def within_forecast_range(dates: Sequence[str], today_: date) -> bool:
    first = _parse_date(dates[0]) if dates else None
    return first is not None and first <= today_ + timedelta(days=FORECAST_RANGE_DAYS)


def _apply_dates(brief: Brief, assumed: set[str], updates: Mapping[str, Any], today_: date) -> None:
    start = _parse_date(updates.get("start_date"))
    end = _parse_date(updates.get("end_date"))
    length = _positive_int(updates.get("length_days"))
    if end is not None and end == _parse_date(brief.get("end_date")):
        end = None
    if length is not None:
        end = None
    if start is None and end is None and length is None:
        return
    old_start = _parse_date(brief.get("start_date"))
    old_length = trip_days(brief) or None
    if start is not None:
        assumed.discard("start_date")
    else:
        start = old_start
    if end is None:
        span = length or old_length
        if span is not None:
            if start is None:
                start = today_ + timedelta(days=DEFAULT_START_OFFSET_DAYS)
                assumed.add("start_date")
            end = start + timedelta(days=span - 1)
    if start is not None:
        brief["start_date"] = start.isoformat()
    if end is not None:
        brief["end_date"] = end.isoformat()


def apply_updates(
    current: Brief, assumed: Sequence[str], updates: Mapping[str, Any], today_: date
) -> tuple[Brief, list[str]]:
    brief = copy.deepcopy(current)
    flagged = set(assumed)
    if (destination := _text(updates.get("destination"))) is not None:
        brief["destination"] = destination
    if (origin := _text(updates.get("origin"))) is not None:
        brief["origin"] = origin
    if (travellers := _positive_int(updates.get("travellers"), MAX_TRAVELLERS)) is not None:
        brief["travellers"] = travellers
        flagged.discard("travellers")
    level = updates.get("budget_level")
    if isinstance(level, str) and level in DAILY_COST_USD:
        brief["budget_level"] = cast("Level", level)
        flagged.discard("budget_level")
    if (amount := _positive_number(updates.get("budget_usd"))) is not None:
        brief["budget_usd"] = amount
    raw_interests = updates.get("interests")
    if isinstance(raw_interests, list):
        interests = list(dict.fromkeys(item.strip() for item in raw_interests if _text(item)))
        if interests:
            brief["interests"] = interests
    _apply_dates(brief, flagged, updates, today_)
    if "travellers" not in brief:
        brief["travellers"] = DEFAULT_TRAVELLERS
        flagged.add("travellers")
    return brief, sorted(flagged)


def _norm(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _norm_all(values: Sequence[str] | None) -> list[str]:
    return sorted(_norm(value) for value in values or [])


def _interest_set(values: object) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {_norm(value) for value in values if _text(value)}


def diff_brief(current: Brief, updated: Mapping[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for key in ("destination", "origin", "budget_level"):
        text = _text(updated.get(key))
        if text is not None and _norm(text) != _norm(current.get(key)):
            changed[key] = text
    for key in ("start_date", "end_date"):
        parsed = _parse_date(updated.get(key))
        if parsed is not None and parsed != _parse_date(current.get(key)):
            changed[key] = parsed.isoformat()
    length = _positive_int(updated.get("length_days"))
    if length is not None and length != trip_days(current):
        changed["length_days"] = length
    travellers = _positive_int(updated.get("travellers"))
    if travellers is not None and travellers != current.get("travellers"):
        changed["travellers"] = travellers
    amount = _positive_number(updated.get("budget_usd"))
    if amount is not None and amount != current.get("budget_usd"):
        changed["budget_usd"] = amount
    interests = _interest_set(updated.get("interests"))
    if interests and interests != _interest_set(current.get("interests")):
        changed["interests"] = updated["interests"]
    return changed


def changed_fields(before: Brief, after: Brief) -> set[ChangedField]:
    changed: set[ChangedField] = set()
    if _norm(before.get("destination")) != _norm(after.get("destination")):
        changed.add("destination")
    if _norm(before.get("origin")) != _norm(after.get("origin")):
        changed.add("origin")
    if before.get("travellers") != after.get("travellers"):
        changed.add("travellers")
    if (before.get("start_date"), before.get("end_date")) != (
        after.get("start_date"),
        after.get("end_date"),
    ):
        changed.add("dates")
    if _norm_all(before.get("interests")) != _norm_all(after.get("interests")):
        changed.add("interests")
    if (before.get("budget_level"), before.get("budget_usd")) != (
        after.get("budget_level"),
        after.get("budget_usd"),
    ):
        changed.add("budget")
    return changed


def budget_hint(brief: Brief) -> str:
    parts: list[str] = []
    if (amount := brief.get("budget_usd")) is not None:
        parts.append(f"${amount:,.0f} in total")
    if (level := brief.get("budget_level")) is not None:
        parts.append(f"{level} level")
    return f"The trip budget is {', '.join(parts)}." if parts else ""


def daily_cost(brief: Brief) -> float:
    rate = DAILY_COST_USD[brief.get("budget_level", DEFAULT_LEVEL)]
    return float(rate * brief.get("travellers", DEFAULT_TRAVELLERS))


def budget(brief: Brief, hotel_price: float, flight_price: float, days: int) -> Budget:
    daily = round(daily_cost(brief) * days, 2)
    total = round(flight_price + hotel_price + daily, 2)
    stated = brief.get("budget_usd")
    allowance = round(stated, 2) if stated is not None else None
    return Budget(
        flightUsd=round(flight_price, 2),
        hotelUsd=round(hotel_price, 2),
        dailyUsd=daily,
        totalUsd=total,
        budgetUsd=allowance,
        overBudget=allowance is not None and total > allowance,
    )


def prefer_new(options: Sequence[Option], seen_ids: Sequence[str]) -> list[Option]:
    fresh = sorted((option for option in options if option["id"] not in seen_ids), key=_price)
    known = [option for option in options if option["id"] in seen_ids]
    return [*fresh, *known]


def _price(option: Option) -> float:
    return option["priceUsd"]


def weather_text(day: Mapping[str, Any]) -> str | None:
    high = day.get("highC")
    low = day.get("lowC")
    rain = day.get("rainChancePct")
    parts: list[str] = []
    if isinstance(high, int | float) and isinstance(low, int | float):
        parts.append(f"{low:.0f} to {high:.0f} °C")
    if isinstance(rain, int | float):
        parts.append(f"{rain:.0f}% chance of rain")
    return ", ".join(parts) or None


def forecast_by_date(data: object) -> dict[str, str]:
    days = data.get("days") if isinstance(data, dict) else data
    if not isinstance(days, list):
        return {}
    forecast: dict[str, str] = {}
    for entry in days:
        if not isinstance(entry, dict):
            continue
        day_date = entry.get("date")
        text = weather_text(entry)
        if isinstance(day_date, str) and text is not None:
            forecast[day_date] = text
    return forecast


def fill_weather(days: Sequence[Day], forecast: Mapping[str, str]) -> list[Day]:
    return [
        Day(
            date=day["date"],
            activities=list(day["activities"]),
            weather=forecast.get(day["date"], NO_FORECAST),
            costUsd=day["costUsd"],
        )
        for day in days
    ]
