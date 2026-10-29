import json
from datetime import date, timedelta

import pytest

from src.utils.messages import parse_options, tool_text
from src.utils.trip import (
    ALLOWANCE_USD,
    DAILY_COST_USD,
    NO_FORECAST,
    Brief,
    Change,
    ChangedField,
    Day,
    Option,
    Part,
    apply_updates,
    budget,
    changed_fields,
    daily_cost,
    fill_weather,
    forecast_by_date,
    merge_parts,
    missing_fields,
    prefer_new,
    redo_for,
    trip_dates,
    within_forecast_range,
)

TODAY = date(2026, 10, 27)
ALL_PARTS: list[Part] = ["flights", "hotels", "itinerary"]


def complete_brief() -> Brief:
    return Brief(
        destination="Tokyo",
        origin="Singapore",
        start_date="2026-11-10",
        end_date="2026-11-14",
        travellers=1,
        budget_level="mid",
    )


def option(option_id: str, price: float) -> Option:
    return Option(
        id=option_id, summary=option_id, priceUsd=price, deepLink=f"https://x.example/{option_id}"
    )


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("dates", ALL_PARTS),
        ("destination", ALL_PARTS),
        ("travellers", ALL_PARTS),
        ("origin", ["flights"]),
        ("interests", ["itinerary"]),
        ("budget", []),
    ],
)
def test_each_changed_field_redoes_exactly_its_row_of_the_requirement(
    field: ChangedField, expected: list[Part]
) -> None:
    assert redo_for({field}, None) == expected


@pytest.mark.parametrize(
    ("change", "expected"),
    [("flights", ["flights", "itinerary"]), ("hotel", ["hotels"])],
)
def test_a_requested_change_redoes_its_row_of_the_requirement(
    change: Change, expected: list[Part]
) -> None:
    assert redo_for(set(), change) == expected


def test_several_changes_redo_the_union_in_planning_order() -> None:
    assert redo_for({"origin", "interests"}, None) == ["flights", "itinerary"]
    assert redo_for({"budget"}, "hotel") == ["hotels"]
    assert redo_for({"origin"}, "hotel") == ["flights", "hotels"]


def test_nothing_changed_redoes_nothing() -> None:
    assert redo_for(set(), None) == []


def test_merge_parts_keeps_planning_order() -> None:
    assert merge_parts(["itinerary"], ["flights"]) == ["flights", "itinerary"]
    assert merge_parts([], []) == []


def test_missing_fields_lists_what_the_brief_still_needs() -> None:
    assert missing_fields(Brief(destination="Tokyo")) == ["origin", "dates or trip length"]
    assert missing_fields(complete_brief()) == []


def test_daily_cost_table_is_per_traveller_per_day() -> None:
    assert DAILY_COST_USD == {"low": 40, "mid": 90, "high": 180}
    assert ALLOWANCE_USD == {"low": 100, "mid": 250, "high": 500}
    brief = complete_brief()
    brief["travellers"] = 3
    brief["budget_level"] = "high"
    assert daily_cost(brief) == 540


def test_level_budget_is_hotel_plus_table_daily_against_the_allowance() -> None:
    brief = complete_brief()
    brief["travellers"] = 2

    result = budget(brief, hotel_price=600, days=5)

    assert result == {
        "dailyUsd": 900.0,
        "totalUsd": 1500.0,
        "budgetUsd": 2500.0,
        "overBudget": False,
    }


def test_over_the_level_allowance_is_flagged() -> None:
    brief = complete_brief()
    brief["budget_level"] = "low"

    result = budget(brief, hotel_price=300, days=2)

    assert result["dailyUsd"] == 80
    assert result["totalUsd"] == 380
    assert result["budgetUsd"] == 200
    assert result["overBudget"] is True


@pytest.mark.parametrize(("stated", "over"), [(800, True), (850, False), (5000, False)])
def test_a_typed_amount_replaces_the_allowance_and_keeps_the_stated_level(
    stated: float, over: bool
) -> None:
    brief = complete_brief()
    brief["budget_usd"] = stated

    result = budget(brief, hotel_price=400, days=5)

    assert result["dailyUsd"] == 450
    assert result["totalUsd"] == 850
    assert result["budgetUsd"] == stated
    assert result["overBudget"] is over


def test_a_typed_amount_without_a_level_uses_mid() -> None:
    brief = Brief(travellers=1, budget_usd=300)

    assert budget(brief, hotel_price=0, days=2)["dailyUsd"] == 180


def test_prefer_new_puts_unseen_options_first_cheapest_first() -> None:
    options = [option("a", 300), option("b", 100), option("c", 200)]

    assert [o["id"] for o in prefer_new(options, ["a"])] == ["b", "c", "a"]
    assert [o["id"] for o in prefer_new(options, [])] == ["b", "c", "a"]
    assert [o["id"] for o in prefer_new(options, ["a", "b", "c"])] == ["a", "b", "c"]


def day(day_date: str, cost: float) -> Day:
    return Day(date=day_date, activities=["walk"], weather=NO_FORECAST, costUsd=cost)


def test_days_without_a_forecast_get_the_no_forecast_text() -> None:
    days = [day("2026-11-10", 10), day("2026-11-11", 20)]
    entry = {"date": "2026-11-10", "highC": 20, "lowC": 12.4, "rainChancePct": 30}
    forecast = forecast_by_date({"city": "Tokyo", "days": [entry]})

    filled = fill_weather(days, forecast)

    assert filled[0]["weather"] == "12 to 20 °C, 30% chance of rain"
    assert filled[1]["weather"] == "no forecast available yet"
    assert [entry["costUsd"] for entry in filled] == [10, 20]


@pytest.mark.parametrize("data", [None, "oops", {"days": "x"}, {"days": [1, {"date": 5}]}])
def test_unusable_forecast_data_leaves_every_day_without_weather(data: object) -> None:
    assert forecast_by_date(data) == {}


def test_forecast_range_matches_the_q_query_window_of_fifteen_days() -> None:
    near = [(TODAY + timedelta(days=15)).isoformat()]
    far = [(TODAY + timedelta(days=16)).isoformat()]

    assert within_forecast_range(near, TODAY) is True
    assert within_forecast_range(far, TODAY) is False
    assert within_forecast_range([], TODAY) is False


def test_a_length_without_dates_starts_in_two_weeks_and_flags_the_defaults() -> None:
    brief, assumed = apply_updates(
        Brief(), [], {"destination": "Tokyo", "length_days": 5, "budget_level": "high"}, TODAY
    )

    start = TODAY + timedelta(days=14)
    assert brief["start_date"] == start.isoformat()
    assert brief["end_date"] == (start + timedelta(days=4)).isoformat()
    assert brief["travellers"] == 1
    assert brief["budget_level"] == "high"
    assert assumed == ["start_date", "travellers"]


def test_explicit_values_clear_their_assumed_flag() -> None:
    brief, assumed = apply_updates(
        Brief(), [], {"destination": "Rome", "length_days": 3, "origin": "Oslo"}, TODAY
    )
    assert assumed == ["budget_level", "start_date", "travellers"]

    brief, assumed = apply_updates(
        brief, assumed, {"start_date": "2026-12-01", "travellers": 3, "budget_level": "low"}, TODAY
    )

    assert brief["start_date"] == "2026-12-01"
    assert brief["end_date"] == "2026-12-03"
    assert brief["travellers"] == 3
    assert assumed == []


def test_a_new_length_keeps_the_start_and_a_new_start_keeps_the_length() -> None:
    brief, _ = apply_updates(complete_brief(), [], {"length_days": 7}, TODAY)
    assert (brief["start_date"], brief["end_date"]) == ("2026-11-10", "2026-11-16")

    brief, _ = apply_updates(complete_brief(), [], {"start_date": "2026-12-01"}, TODAY)
    assert (brief["start_date"], brief["end_date"]) == ("2026-12-01", "2026-12-05")


def test_a_start_only_change_keeps_the_old_length() -> None:
    brief, _ = apply_updates(complete_brief(), [], {"start_date": "2026-11-01"}, TODAY)

    assert (brief["start_date"], brief["end_date"]) == ("2026-11-01", "2026-11-05")


def test_a_stated_length_wins_over_an_extracted_end_date() -> None:
    updates = {"start_date": "2026-11-01", "length_days": 5, "end_date": "2026-11-14"}

    brief, _ = apply_updates(complete_brief(), [], updates, TODAY)

    assert (brief["start_date"], brief["end_date"]) == ("2026-11-01", "2026-11-05")


def test_an_end_date_copied_from_the_brief_is_ignored() -> None:
    updates = {"start_date": "2026-11-01", "end_date": "2026-11-14"}

    brief, _ = apply_updates(complete_brief(), [], updates, TODAY)

    assert (brief["start_date"], brief["end_date"]) == ("2026-11-01", "2026-11-05")


def test_an_explicit_new_end_date_still_applies() -> None:
    updates = {"start_date": "2026-11-01", "end_date": "2026-11-20"}

    brief, _ = apply_updates(complete_brief(), [], updates, TODAY)

    assert (brief["start_date"], brief["end_date"]) == ("2026-11-01", "2026-11-20")


def test_junk_updates_are_ignored() -> None:
    brief, _ = apply_updates(
        complete_brief(),
        [],
        {"destination": "  ", "travellers": -2, "budget_level": "luxury", "start_date": "soon"},
        TODAY,
    )

    assert brief == complete_brief()


def test_changed_fields_compares_two_briefs() -> None:
    before = complete_brief()
    after = complete_brief()
    after["destination"] = " tokyo "
    assert changed_fields(before, after) == set()

    after["origin"] = "Osaka"
    after["travellers"] = 2
    after["end_date"] = "2026-11-20"
    after["interests"] = ["food"]
    after["budget_usd"] = 900
    assert changed_fields(before, after) == {
        "origin",
        "travellers",
        "dates",
        "interests",
        "budget",
    }


def test_a_budget_level_change_alone_is_a_budget_change() -> None:
    before = complete_brief()
    after = complete_brief()
    after["budget_level"] = "high"

    assert changed_fields(before, after) == {"budget"}


def test_trip_dates_lists_one_date_per_day() -> None:
    assert trip_dates(complete_brief()) == [
        "2026-11-10",
        "2026-11-11",
        "2026-11-12",
        "2026-11-13",
        "2026-11-14",
    ]
    assert trip_dates(Brief()) == []


def listing(*items: dict[str, object]) -> str:
    return json.dumps(list(items))


def raw(option_id: str, **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": option_id,
        "summary": f"Option {option_id}",
        "priceUsd": 100.456,
        "deepLink": f"https://book.example/{option_id}",
    }
    return {**item, **overrides}


def test_options_are_parsed_from_the_raw_tool_text_untouched() -> None:
    text = listing(raw("a"), raw("b", priceUsd=200))

    assert parse_options(text) == [
        {
            "id": "a",
            "summary": "Option a",
            "priceUsd": 100.46,
            "deepLink": "https://book.example/a",
        },
        {
            "id": "b",
            "summary": "Option b",
            "priceUsd": 200.0,
            "deepLink": "https://book.example/b",
        },
    ]


def test_options_are_also_found_under_a_wrapping_key() -> None:
    assert [o["id"] for o in parse_options(json.dumps({"options": [raw("a")]}))] == ["a"]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "null",
        listing(raw("a", deepLink="http://insecure.example")),
        listing(raw("a", deepLink=None)),
        listing(raw("a", priceUsd="cheap")),
        listing(raw("a", priceUsd=True)),
        listing(raw("a", summary=None)),
        listing(raw("")),
        listing({"id": 1}),
    ],
)
def test_options_without_a_valid_id_price_summary_or_https_link_are_dropped(text: str) -> None:
    assert parse_options(text) == []


def test_duplicate_option_ids_keep_the_first() -> None:
    options = parse_options(listing(raw("a"), raw("a", summary="again")))

    assert [o["summary"] for o in options] == ["Option a"]


def test_tool_text_takes_the_latest_successful_call_of_that_name() -> None:
    calls = [
        ("flights__search", "old", True),
        ("flights__search", "newest", True),
        ("flights__search", "failed", False),
        ("hotels__search", "other", True),
    ]

    assert tool_text(calls, "flights__search") == "newest"
    assert tool_text(calls, "weather__forecast") == ""
