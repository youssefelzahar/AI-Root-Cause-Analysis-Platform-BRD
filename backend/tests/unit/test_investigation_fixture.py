"""The acceptance fixture's marginals, checked from the file itself.

The section 23 answer only falls out of the data if every per-airline,
per-sentiment and per-cabin total is exactly right. Those were solved for rather
than typed, so they are machine-checked here: a hand edit to the CSV that breaks
the answer fails this file rather than the acceptance test, where the cause would
be far harder to see.
"""

import csv
from collections import defaultdict
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "investigation_airline.csv"

PREV_DATE = "2026-06-15"
CURR_DATE = "2026-07-15"

# The marginals that make the specification's answer true.
PREV_AIRLINE = {
    "Singapore Airlines": 12,
    "LOT Polish Airlines": 8,
    "Saudia": 8,
    "Air India": 7,
    "Korean Air": 6,
    "Emirates": 6,
    "TAP Portugal": 6,
    "Turkish Airlines": 6,
    "Aer Lingus": 6,
}
CURR_AIRLINE = {
    "LOT Polish Airlines": 6,
    "Saudia": 6,
    "Air India": 5,
    "Korean Air": 5,
    "Emirates": 5,
    "TAP Portugal": 8,
    "Turkish Airlines": 8,
    "Aer Lingus": 7,
}
PREV_SENTIMENT = {"positive": 30, "neutral": 20, "negative": 15}
CURR_SENTIMENT = {"positive": 18, "neutral": 19, "negative": 13}
PREV_CABIN = {"Economy": 35, "Premium": 18, "Business": 12}
CURR_CABIN = {"Economy": 25, "Premium": 15, "Business": 10}


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with FIXTURE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _totals(rows: list[dict[str, str]], date: str, column: str) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["review_date"] == date:
            out[row[column]] += int(row["value_for_money"])
    return dict(out)


def test_the_two_period_totals_produce_the_expected_headline(rows) -> None:
    previous = sum(int(r["value_for_money"]) for r in rows if r["review_date"] == PREV_DATE)
    current = sum(int(r["value_for_money"]) for r in rows if r["review_date"] == CURR_DATE)

    assert previous == 65
    assert current == 50
    assert current - previous == -15
    assert (current - previous) / previous * 100 == pytest.approx(-23.0769, abs=0.001)


def test_the_period_row_counts_match_the_specification(rows) -> None:
    assert sum(1 for r in rows if r["review_date"] == PREV_DATE) == 22
    assert sum(1 for r in rows if r["review_date"] == CURR_DATE) == 21
    assert len(rows) == 43


def test_only_two_distinct_dates_exist(rows) -> None:
    """More would make the profiler read the frequency as daily.

    The newest bucket would then look incomplete and be excluded, shifting both
    compared windows back a month and invalidating every expected number.
    """
    assert {r["review_date"] for r in rows} == {PREV_DATE, CURR_DATE}


def test_the_airline_marginals_are_exact(rows) -> None:
    assert _totals(rows, PREV_DATE, "airline") == PREV_AIRLINE
    assert _totals(rows, CURR_DATE, "airline") == CURR_AIRLINE


def test_singapore_airlines_contributes_exactly_eighty_percent(rows) -> None:
    delta = CURR_AIRLINE.get("Singapore Airlines", 0) - PREV_AIRLINE["Singapore Airlines"]
    assert delta == -12
    assert delta / -15 == pytest.approx(0.80)


def test_singapore_airlines_has_no_current_rows_at_all(rows) -> None:
    """GONE is defined on rows, not on a zero value.

    A segment present but measuring zero is a different finding, so the fixture
    has to omit the rows rather than carry zeros.
    """
    current = [r for r in rows if r["review_date"] == CURR_DATE]
    assert not [r for r in current if r["airline"] == "Singapore Airlines"]


def test_singapore_airlines_sits_in_one_sentiment_and_one_cabin(rows) -> None:
    """What makes the drill-down a pure split at both levels."""
    sg = [r for r in rows if r["airline"] == "Singapore Airlines"]
    assert sg
    assert {r["sentiment"] for r in sg} == {"positive"}
    assert {r["cabin"] for r in sg} == {"Economy"}


def test_the_sentiment_and_cabin_marginals_are_exact(rows) -> None:
    assert _totals(rows, PREV_DATE, "sentiment") == PREV_SENTIMENT
    assert _totals(rows, CURR_DATE, "sentiment") == CURR_SENTIMENT
    assert _totals(rows, PREV_DATE, "cabin") == PREV_CABIN
    assert _totals(rows, CURR_DATE, "cabin") == CURR_CABIN


def test_every_measure_value_is_a_positive_integer(rows) -> None:
    for row in rows:
        assert int(row["value_for_money"]) >= 1, row
