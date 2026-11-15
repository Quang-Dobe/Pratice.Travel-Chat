import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from typing import Any, Literal, cast

from docx import Document
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from src.graph.state import PlanCard
from src.utils.trip import Brief, Budget, Day, Option

type ExportFormat = Literal["html", "docx", "xlsx", "csv"]
type Cell = str | float

DRAFT_BANNER = "Draft — not approved"
COLUMNS = ("Section", "Date", "Item", "Details", "Weather", "Cost in USD")
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
NON_SLUG = re.compile(r"[^a-z0-9]+")
MAX_SLUG_LENGTH = 40
SHEET_NAME = "Plan"

FORMAT_ALIASES: dict[str, ExportFormat] = {
    "html": "html",
    "docx": "docx",
    "word": "docx",
    "xlsx": "xlsx",
    "excel": "xlsx",
    "csv": "csv",
}
CONTENT_TYPES: dict[str, str] = {
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}
DEFAULT_CONTENT_TYPE = "application/octet-stream"

HTML_STYLE = (
    "body{font-family:sans-serif;max-width:48rem;margin:2rem auto;padding:0 1rem}"
    "table{border-collapse:collapse;width:100%}"
    "th,td{border:1px solid #bbb;padding:.4rem;text-align:left;vertical-align:top}"
    ".draft{background:#fff3cd;border:1px solid #e0a800;padding:.5rem;font-weight:bold}"
)


@dataclass(frozen=True)
class Plan:
    destination: str
    draft: bool
    facts: list[str]
    flights: list[Option]
    hotels: list[Option]
    days: list[Day]
    budget: Budget


def parse_format(text: str) -> ExportFormat | None:
    return FORMAT_ALIASES.get(text.strip().lower())


def content_type(fmt: str) -> str:
    return CONTENT_TYPES.get(fmt, DEFAULT_CONTENT_TYPE)


def is_draft(card: PlanCard) -> bool:
    return card["status"] != "approved"


def _slug(destination: str) -> str:
    slug = NON_SLUG.sub("-", destination.lower()).strip("-")[:MAX_SLUG_LENGTH].strip("-")
    return slug or "trip"


def file_name(card: PlanCard, brief: Brief, fmt: ExportFormat) -> str:
    suffix = "-draft" if is_draft(card) else ""
    return f"{_slug(brief.get('destination', ''))}-plan{suffix}.{fmt}"


def _clean(text: str) -> str:
    return CONTROL_CHARACTERS.sub("", text)


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _facts(brief: Brief) -> list[str]:
    facts: list[str] = []
    if destination := brief.get("destination"):
        facts.append(f"Destination: {destination}")
    if origin := brief.get("origin"):
        facts.append(f"From: {origin}")
    if brief.get("start_date") and brief.get("end_date"):
        facts.append(f"Dates: {brief['start_date']} to {brief['end_date']}")
    if travellers := brief.get("travellers"):
        facts.append(f"Travellers: {travellers}")
    return facts


def _chosen(options: Sequence[Option], picked_id: str, draft: bool) -> list[Option]:
    if draft:
        return list(options)
    return [option for option in options if option["id"] == picked_id]


def build_plan(card: PlanCard, brief: Brief) -> Plan:
    draft = is_draft(card)
    return Plan(
        destination=brief.get("destination", ""),
        draft=draft,
        facts=_facts(brief),
        flights=_chosen(card["flights"], card["picked"]["flightId"], draft),
        hotels=_chosen(card["hotels"], card["picked"]["hotelId"], draft),
        days=list(card["days"]),
        budget=card["budget"],
    )


def _title(plan: Plan) -> str:
    return f"Trip plan: {plan.destination}" if plan.destination else "Trip plan"


def _option_line(option: Option) -> str:
    return f"{option['summary']} — {_money(option['priceUsd'])} — {option['deepLink']}"


def _day_activities(day: Day) -> str:
    return "; ".join(day["activities"])


def _day_stops(day: Day) -> str:
    return ", ".join(stop["name"] for stop in day.get("stops", []))


def _budget_lines(budget: Budget) -> list[str]:
    lines = [
        f"Flight: {_money(budget['flightUsd'])}",
        f"Hotel: {_money(budget['hotelUsd'])}",
        f"Daily costs: {_money(budget['dailyUsd'])}",
        f"Total: {_money(budget['totalUsd'])}",
    ]
    if budget["budgetUsd"] is not None:
        lines.append(f"Budget: {_money(budget['budgetUsd'])}")
    return lines


def _link(url: str) -> str:
    safe = escape(url)
    return f'<a href="{safe}">{safe}</a>' if url.startswith("https://") else safe


def _html_options(title: str, options: Sequence[Option]) -> list[str]:
    parts = [f"<h2>{escape(title)}</h2>"]
    if not options:
        return [*parts, "<p>None.</p>"]
    items = (
        f"<li>{escape(option['summary'])} — {_money(option['priceUsd'])} — "
        f"{_link(option['deepLink'])}</li>"
        for option in options
    )
    return [*parts, "<ul>", *items, "</ul>"]


def _html_days(days: Sequence[Day]) -> list[str]:
    head = "<tr><th>Date</th><th>Activities</th><th>Stops</th><th>Weather</th><th>Cost</th></tr>"
    rows = (
        f"<tr><td>{escape(day['date'])}</td><td>{escape(_day_activities(day))}</td>"
        f"<td>{escape(_day_stops(day))}</td><td>{escape(day['weather'])}</td>"
        f"<td>{_money(day['costUsd'])}</td></tr>"
        for day in days
    )
    return ["<h2>Days</h2>", "<table>", head, *rows, "</table>"]


def _html(plan: Plan) -> bytes:
    title = escape(_title(plan))
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{title}</title><style>{HTML_STYLE}</style></head><body>",
        f"<h1>{title}</h1>",
    ]
    if plan.draft:
        parts.append(f'<p class="draft">{escape(DRAFT_BANNER)}</p>')
    parts.extend(f"<p>{escape(fact)}</p>" for fact in plan.facts)
    parts.extend(_html_options("Flights", plan.flights))
    parts.extend(_html_options("Hotels", plan.hotels))
    parts.extend(_html_days(plan.days))
    parts.append("<h2>Costs</h2><ul>")
    parts.extend(f"<li>{escape(line)}</li>" for line in _budget_lines(plan.budget))
    parts.append("</ul></body></html>")
    return "\n".join(parts).encode("utf-8")


def _docx(plan: Plan) -> bytes:
    document = Document()
    document.add_heading(_clean(_title(plan)), level=1)
    if plan.draft:
        document.add_paragraph().add_run(DRAFT_BANNER).bold = True
    for fact in plan.facts:
        document.add_paragraph(_clean(fact))
    for heading, options in (("Flights", plan.flights), ("Hotels", plan.hotels)):
        document.add_heading(heading, level=2)
        if not options:
            document.add_paragraph("None.")
        for option in options:
            document.add_paragraph(_clean(_option_line(option)), style="List Bullet")
    document.add_heading("Days", level=2)
    table = document.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    for cell, text in zip(
        table.rows[0].cells, ("Date", "Activities", "Stops", "Weather", "Cost"), strict=True
    ):
        cell.text = text
    for day in plan.days:
        values = (
            day["date"],
            _day_activities(day),
            _day_stops(day),
            day["weather"],
            _money(day["costUsd"]),
        )
        for cell, text in zip(table.add_row().cells, values, strict=True):
            cell.text = _clean(text)
    document.add_heading("Costs", level=2)
    for line in _budget_lines(plan.budget):
        document.add_paragraph(line, style="List Bullet")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _is_formula_like(text: str) -> bool:
    return text.startswith(FORMULA_STARTS)


def neutralise(text: str) -> str:
    cleaned = _clean(text)
    return f"'{cleaned}" if _is_formula_like(cleaned) else cleaned


def _cell(value: Cell) -> Cell:
    return _clean(value) if isinstance(value, str) else round(float(value), 2)


def _day_row(day: Day) -> list[Cell]:
    stops = _day_stops(day)
    details = f"Stops: {stops}" if stops else ""
    return ["Day", day["date"], _day_activities(day), details, day["weather"], day["costUsd"]]


def _option_row(label: str, option: Option) -> list[Cell]:
    summary = f"{label}: {option['summary']}"
    return ["Cost", "", summary, option["deepLink"], "", option["priceUsd"]]


def rows(plan: Plan) -> list[list[Cell]]:
    table: list[list[Cell]] = [list(COLUMNS)]
    table.extend(_day_row(day) for day in plan.days)
    table.extend(_option_row("Flight", option) for option in plan.flights)
    table.extend(_option_row("Hotel", option) for option in plan.hotels)
    table.append(["Cost", "", "Daily costs", "", "", plan.budget["dailyUsd"]])
    note = DRAFT_BANNER if plan.draft else ""
    table.append(["Cost", "", "Total", note, "", plan.budget["totalUsd"]])
    header, *body = table
    return [header, *([_cell(value) for value in row] for row in body)]


def _xlsx_cell(sheet: Any, value: Cell) -> Any:
    if not isinstance(value, str) or not _is_formula_like(value):
        return value
    cell = cast("Any", WriteOnlyCell(sheet, value))
    cell.data_type = "s"
    cell.quotePrefix = True
    return cell


def _xlsx(plan: Plan) -> bytes:
    workbook = Workbook(write_only=True)
    sheet: Any = workbook.create_sheet(SHEET_NAME)
    for row in rows(plan):
        sheet.append([_xlsx_cell(sheet, value) for value in row])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _csv_value(value: Cell) -> str:
    return f"{value:.2f}" if isinstance(value, float) else neutralise(str(value))


def _csv(plan: Plan) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for row in rows(plan):
        writer.writerow(_csv_value(value) for value in row)
    return buffer.getvalue().encode("utf-8-sig")


def render(card: PlanCard, brief: Brief, fmt: ExportFormat) -> bytes:
    plan = build_plan(card, brief)
    match fmt:
        case "html":
            return _html(plan)
        case "docx":
            return _docx(plan)
        case "xlsx":
            return _xlsx(plan)
        case "csv":
            return _csv(plan)
