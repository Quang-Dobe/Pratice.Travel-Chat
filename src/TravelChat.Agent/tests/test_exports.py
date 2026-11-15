import csv
import io
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from docx import Document
from langchain_core.tools import BaseTool
from openpyxl import load_workbook

from src.exports import repository
from src.exports import tool as export_module
from src.exports.render import (
    COLUMNS,
    DRAFT_BANNER,
    ExportFormat,
    content_type,
    file_name,
    neutralise,
    parse_format,
    render,
)
from src.exports.tool import NO_CARD, ExportItem, export_tool
from src.graph.state import CardStatus, Picked, PlanCard
from src.main import DictRowPool
from src.utils import tools as tools_module
from src.utils.trip import Brief, Budget, Day, Option, Stop

FORMATS: tuple[ExportFormat, ...] = ("html", "docx", "xlsx", "csv")
BRIEF = Brief(
    destination="Tokyo",
    origin="Singapore",
    start_date="2026-11-10",
    end_date="2026-11-11",
    travellers=2,
)
CONVERSATION = str(uuid4())


def option(option_id: str, summary: str, price: float) -> Option:
    return Option(
        id=option_id, summary=summary, priceUsd=price, deepLink=f"https://x.example/{option_id}"
    )


def make_card(
    status: CardStatus = "open", activities: list[str] | None = None, weather: str = "12 to 20 °C"
) -> PlanCard:
    return PlanCard(
        id="card-1",
        messageId="m1",
        status=status,
        flights=[option("f1", "Flight One", 400.0), option("f2", "Flight Two", 500.0)],
        hotels=[option("h1", "Hotel One", 600.0), option("h2", "Hotel Two", 700.0)],
        picked=Picked(flightId="f2", hotelId="h1"),
        days=[
            Day(
                date="2026-11-10",
                activities=activities or ["Senso-ji", "Tsukiji"],
                weather=weather,
                costUsd=90.0,
                stops=[Stop(name="Senso-ji", lat=35.7, lon=139.8)],
            ),
            Day(date="2026-11-11", activities=["Ueno Park"], weather="mild", costUsd=90.0),
        ],
        budget=Budget(
            flightUsd=500.0,
            hotelUsd=600.0,
            dailyUsd=180.0,
            totalUsd=1280.0,
            budgetUsd=None,
            overBudget=False,
        ),
    )


def docx_text(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def xlsx_rows(data: bytes) -> list[list[Any]]:
    sheet = cast("Any", load_workbook(io.BytesIO(data))["Plan"])
    rows = sheet.iter_rows(values_only=True)
    return [["" if cell is None else cell for cell in row] for row in rows]


def csv_rows(data: bytes) -> list[list[Any]]:
    return [list(row) for row in csv.reader(io.StringIO(data.decode("utf-8-sig")))]


def xlsx_sheet(data: bytes) -> Any:
    return cast("Any", load_workbook(io.BytesIO(data))["Plan"])


def text_of(fmt: ExportFormat, data: bytes) -> str:
    if fmt == "docx":
        return docx_text(data)
    if fmt == "xlsx":
        return "\n".join(str(cell) for row in xlsx_rows(data) for cell in row)
    return data.decode("utf-8")


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_renders_the_days_activities_weather_and_costs(fmt: ExportFormat) -> None:
    text = text_of(fmt, render(make_card("approved"), BRIEF, fmt))

    for expected in ("2026-11-10", "Senso-ji", "Ueno Park", "12 to 20 °C", "mild"):
        assert expected in text
    assert "1280" in text.replace(",", "")


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_draft_carries_the_banner_and_every_flight_and_hotel(fmt: ExportFormat) -> None:
    text = text_of(fmt, render(make_card("open"), BRIEF, fmt))

    assert DRAFT_BANNER in text
    for expected in ("Flight One", "Flight Two", "Hotel One", "Hotel Two"):
        assert expected in text


@pytest.mark.parametrize("fmt", FORMATS)
def test_an_approved_card_carries_only_the_picked_options_and_no_banner(
    fmt: ExportFormat,
) -> None:
    text = text_of(fmt, render(make_card("approved"), BRIEF, fmt))

    assert DRAFT_BANNER not in text
    assert "Flight Two" in text
    assert "Hotel One" in text
    assert "Flight One" not in text
    assert "Hotel Two" not in text


@pytest.mark.parametrize("fmt", ["xlsx", "csv"])
def test_the_table_formats_have_exactly_the_agreed_columns(fmt: ExportFormat) -> None:
    data = render(make_card("open"), BRIEF, fmt)

    rows = xlsx_rows(data) if fmt == "xlsx" else csv_rows(data)

    assert rows[0] == ["Section", "Date", "Item", "Details", "Weather", "Cost in USD"]
    assert tuple(rows[0]) == COLUMNS
    assert {len(row) for row in rows} == {6}


@pytest.mark.parametrize("fmt", ["xlsx", "csv"])
def test_a_draft_table_has_a_day_row_per_day_and_a_cost_row_per_option_daily_and_total(
    fmt: ExportFormat,
) -> None:
    data = render(make_card("open"), BRIEF, fmt)

    rows = xlsx_rows(data) if fmt == "xlsx" else csv_rows(data)

    assert [row[0] for row in rows[1:]] == ["Day"] * 2 + ["Cost"] * 6
    assert [row[2] for row in rows[3:]] == [
        "Flight: Flight One",
        "Flight: Flight Two",
        "Hotel: Hotel One",
        "Hotel: Hotel Two",
        "Daily costs",
        "Total",
    ]
    assert rows[-1][3] == DRAFT_BANNER
    assert rows[-2][3] == ""
    assert [float(row[5]) for row in rows[1:3]] == [90.0, 90.0]
    assert float(rows[-1][5]) == 1280.0
    assert float(rows[-2][5]) == 180.0


@pytest.mark.parametrize("fmt", ["xlsx", "csv"])
def test_an_approved_table_has_the_picked_cost_rows_and_a_plain_total(fmt: ExportFormat) -> None:
    data = render(make_card("approved"), BRIEF, fmt)

    rows = xlsx_rows(data) if fmt == "xlsx" else csv_rows(data)

    assert [row[2] for row in rows[3:]] == [
        "Flight: Flight Two",
        "Hotel: Hotel One",
        "Daily costs",
        "Total",
    ]
    assert rows[-1][3] == ""


@pytest.mark.parametrize("start", ["=", "+", "-", "@"])
def test_csv_cells_that_start_like_a_formula_get_a_leading_apostrophe(start: str) -> None:
    card = make_card("open", activities=[f"{start}SUM(1+1)"])

    rows = csv_rows(render(card, BRIEF, "csv"))

    assert rows[1][2] == f"'{start}SUM(1+1)"


@pytest.mark.parametrize("start", ["=", "+", "-", "@"])
def test_xlsx_cells_that_start_like_a_formula_keep_their_text_and_set_the_quote_prefix(
    start: str,
) -> None:
    card = make_card("open", activities=[f"{start}SUM(1+1)"], weather=f"{start}2 to 5 °C")

    sheet = xlsx_sheet(render(card, BRIEF, "xlsx"))

    assert sheet["C2"].value == f"{start}SUM(1+1)"
    assert sheet["C2"].quotePrefix is True
    assert sheet["C2"].data_type == "s"
    assert sheet["E2"].value == f"{start}2 to 5 °C"
    assert sheet["E2"].quotePrefix is True
    assert sheet["A2"].quotePrefix is False
    assert sheet["E3"].quotePrefix is False


def test_the_csv_starts_with_a_byte_order_mark_and_still_reads_the_six_columns() -> None:
    data = render(make_card("open"), BRIEF, "csv")

    assert data.startswith(b"\xef\xbb\xbf")
    assert csv_rows(data)[0] == ["Section", "Date", "Item", "Details", "Weather", "Cost in USD"]
    assert "12 to 20 °C" in data.decode("utf-8-sig")


def test_neutralise_leaves_normal_text_and_empty_text_alone() -> None:
    assert neutralise("Senso-ji") == "Senso-ji"
    assert neutralise("") == ""
    assert neutralise("=1+1") == "'=1+1"


def test_html_escapes_everything_it_writes() -> None:
    card = make_card("open", activities=["<script>alert(1)</script>"])

    html = render(card, BRIEF, "html").decode("utf-8")

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_control_characters_never_break_the_binary_formats() -> None:
    card = make_card("open", activities=["Temple\x00 visit\x0b"])

    binary: tuple[ExportFormat, ...] = ("docx", "xlsx")
    for fmt in binary:
        assert render(card, BRIEF, fmt).startswith(b"PK")


@pytest.mark.parametrize(
    ("destination", "status", "fmt", "expected"),
    [
        ("Tokyo", "open", "csv", "tokyo-plan-draft.csv"),
        ("Tokyo", "approved", "xlsx", "tokyo-plan.xlsx"),
        ("New York, USA!", "approved", "docx", "new-york-usa-plan.docx"),
        ("東京", "open", "html", "trip-plan-draft.html"),
        ("", "approved", "html", "trip-plan.html"),
    ],
)
def test_file_names_are_a_destination_slug_with_a_draft_suffix(
    destination: str, status: CardStatus, fmt: ExportFormat, expected: str
) -> None:
    brief = Brief(destination=destination)

    assert file_name(make_card(status), brief, fmt) == expected


def test_a_file_name_slug_is_capped() -> None:
    brief = Brief(destination="a" * 200)

    assert file_name(make_card("approved"), brief, "csv") == f"{'a' * 40}-plan.csv"


def test_formats_parse_by_name_and_common_aliases() -> None:
    assert parse_format(" Excel ") == "xlsx"
    assert parse_format("DOCX") == "docx"
    assert parse_format("csv") == "csv"
    assert parse_format("pdf") is None


def test_every_format_has_its_own_content_type() -> None:
    types = {content_type(fmt) for fmt in FORMATS}

    assert len(types) == 4
    assert content_type("html").startswith("text/html")
    assert content_type("unknown") == "application/octet-stream"


class Writer:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class Inserts:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.export_id = uuid4()

    async def insert(self, *args: Any) -> UUID:
        self.calls.append(args)
        return self.export_id


@pytest.fixture
def writer(monkeypatch: pytest.MonkeyPatch) -> Writer:
    events = Writer()
    monkeypatch.setattr(export_module, "get_stream_writer", lambda: events)
    monkeypatch.setattr(tools_module, "get_stream_writer", lambda: events)
    return events


@pytest.fixture
def inserts(monkeypatch: pytest.MonkeyPatch) -> Inserts:
    recorded = Inserts()
    monkeypatch.setattr(repository, "insert", recorded.insert)
    return recorded


POOL = cast("DictRowPool", object())


def build_tool(
    card: PlanCard | None, made: list[ExportItem], pool: DictRowPool | None = POOL
) -> BaseTool:
    return export_tool(card, BRIEF, CONVERSATION, pool, made)


async def test_the_tool_saves_the_file_announces_it_and_never_returns_the_link(
    writer: Writer, inserts: Inserts
) -> None:
    made: list[ExportItem] = []

    result = await build_tool(make_card("open"), made).ainvoke({"format": "excel"})

    assert result == "File ready: tokyo-plan-draft.xlsx."
    assert "/api/" not in result
    expected = ExportItem(
        id=str(inserts.export_id),
        format="xlsx",
        fileName="tokyo-plan-draft.xlsx",
        url=f"/api/conversations/{CONVERSATION}/exports/{inserts.export_id}",
        draft=True,
    )
    assert made == [expected]
    [artifact] = [event for event in writer.events if event["event"] == "artifact"]
    assert artifact == {"event": "artifact", **expected}
    [call] = inserts.calls
    assert call[:5] == (POOL, UUID(CONVERSATION), "card-1", "xlsx", "tokyo-plan-draft.xlsx")
    assert call[5].startswith(b"PK")
    assert call[6] is True


async def test_an_approved_card_is_saved_as_a_final_file(writer: Writer, inserts: Inserts) -> None:
    made: list[ExportItem] = []

    await build_tool(make_card("approved"), made).ainvoke({"format": "csv"})

    assert made[0]["draft"] is False
    assert made[0]["fileName"] == "tokyo-plan.csv"
    assert inserts.calls[0][6] is False


async def test_without_a_card_the_tool_says_so_and_makes_no_file(
    writer: Writer, inserts: Inserts
) -> None:
    made: list[ExportItem] = []

    result = await build_tool(None, made).ainvoke({"format": "csv"})

    assert result == NO_CARD
    assert made == []
    assert inserts.calls == []
    assert all(event["event"] != "artifact" for event in writer.events)


@pytest.mark.parametrize("bad", ["pdf", ""])
async def test_an_unknown_format_is_an_error_text_and_makes_no_file(
    writer: Writer, inserts: Inserts, bad: str
) -> None:
    made: list[ExportItem] = []

    result = await build_tool(make_card(), made).ainvoke({"format": bad})

    assert "Unknown format" in result
    assert made == []
    assert inserts.calls == []


async def test_without_a_database_pool_the_tool_reports_the_file_was_not_saved(
    writer: Writer, inserts: Inserts
) -> None:
    made: list[ExportItem] = []

    result = await build_tool(make_card(), made, None).ainvoke({"format": "csv"})

    assert "could not be saved" in result
    assert made == []


async def test_a_database_error_on_save_is_the_same_not_saved_error_and_makes_no_item(
    writer: Writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(*args: Any) -> UUID:
        raise RuntimeError("connection lost")

    monkeypatch.setattr(repository, "insert", failing)
    made: list[ExportItem] = []

    result = await build_tool(make_card(), made).ainvoke({"format": "csv"})

    assert "could not be saved" in result
    assert made == []
    assert all(event["event"] != "artifact" for event in writer.events)


async def test_the_tool_announces_its_use_through_the_shared_event_wrapper(
    writer: Writer, inserts: Inserts
) -> None:
    await build_tool(make_card(), []).ainvoke({"format": "csv"})

    statuses = [event["status"] for event in writer.events if event["event"] == "tool_call"]
    assert statuses == ["started", "finished"]


def test_the_tool_is_named_for_the_artifact_agent_spec() -> None:
    assert build_tool(None, []).name == "artifact__export"
