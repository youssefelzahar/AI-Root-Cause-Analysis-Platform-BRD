"""SQL expression builders. Assertions are on generated SQL, nothing executes."""

import pytest

from app.analysis.rca import casting
from app.db.models.enums import Aggregation


@pytest.mark.parametrize(
    "physical",
    ["BIGINT", "INTEGER", "DOUBLE", "DECIMAL(18,2)", "FLOAT", "HUGEINT", "UBIGINT"],
)
def test_numeric_columns_are_recognised_regardless_of_width(physical):
    assert casting.is_numeric_physical(physical)


@pytest.mark.parametrize("physical", ["DATE", "TIMESTAMP", "TIMESTAMP_MS", "TIMESTAMP WITH TIME ZONE"])
def test_temporal_columns_are_recognised(physical):
    assert casting.is_temporal_physical(physical)


def test_a_numeric_measure_is_widened_but_not_cleaned():
    """A clean column must not pay for regexp_replace on every row."""
    sql = casting.measure_expression("revenue", "BIGINT", Aggregation.SUM)
    assert sql == 'CAST("revenue" AS DOUBLE)'
    assert "regexp_replace" not in sql


def test_a_decimal_measure_is_widened_to_double():
    """A DECIMAL sum can overflow its own declared precision."""
    assert casting.measure_expression("revenue", "DECIMAL(9,2)", Aggregation.SUM) == (
        'CAST("revenue" AS DOUBLE)'
    )


def test_a_text_measure_tries_a_native_cast_before_cleaning():
    """This is the Excel and SQL Server path: every column arrives as a string."""
    sql = casting.measure_expression("revenue", "VARCHAR", Aggregation.SUM)
    assert "TRY_CAST(trim(\"revenue\") AS DOUBLE)" in sql
    assert "regexp_replace" in sql  # the rescue for $1,200 and (500)
    assert sql.index("TRY_CAST(trim") < sql.index("regexp_replace")


def test_a_text_measure_folds_null_sentinels_to_null():
    sql = casting.measure_expression("revenue", "VARCHAR", Aggregation.SUM)
    assert "'n/a'" in sql


def test_counting_aggregations_do_not_force_a_numeric_cast():
    """COUNT DISTINCT over a customer id must not require the id to be a number."""
    sql = casting.measure_expression("customer", "VARCHAR", Aggregation.COUNT_DISTINCT)
    assert "AS DOUBLE" not in sql
    # Sentinels still fold out, or 'n/a' would inflate the distinct count.
    assert "'n/a'" in sql


def test_a_date_measure_has_no_numeric_reading():
    assert casting.measure_expression("order_date", "DATE", Aggregation.SUM) == ""


def test_a_temporal_time_column_normalises_to_timestamp():
    """DATE and every TIMESTAMP width must compare against the same predicates."""
    assert casting.time_expression("order_date", "DATE") == 'CAST("order_date" AS TIMESTAMP)'
    assert casting.time_expression("order_date", "TIMESTAMP_MS") == (
        'CAST("order_date" AS TIMESTAMP)'
    )


def test_a_text_time_column_tries_iso_then_the_profiler_formats():
    sql = casting.time_expression("order_date", "VARCHAR")
    assert "TRY_CAST(trim(\"order_date\") AS TIMESTAMP)" in sql
    assert "%d/%m/%Y" in sql
    assert "%d-%b-%Y" in sql


def test_a_numeric_time_column_is_rejected_rather_than_guessed_as_an_epoch():
    """Guessing seconds versus milliseconds would silently move the data by
    decades, so this is an error the caller must resolve."""
    assert casting.time_expression("order_date", "BIGINT") == ""


def test_a_dimension_keeps_sql_null_as_its_own_group():
    """Folding NULL into a sentinel string would let a literal '(unknown)' in the
    data merge with genuine nulls, and every row must land in exactly one cell."""
    sql = casting.dimension_expression("region", "VARCHAR")
    assert sql == 'trim("region")'
    assert "COALESCE" not in sql
    assert "unknown" not in sql.lower()


def test_a_non_text_dimension_is_rendered_as_text():
    assert casting.dimension_expression("year", "BIGINT") == 'CAST("year" AS VARCHAR)'


def test_a_column_name_containing_a_quote_is_escaped():
    """Identifiers come from user files, so quoting is not optional."""
    sql = casting.measure_expression('rev"; DROP TABLE t; --', "BIGINT", Aggregation.SUM)
    assert '"rev""; DROP TABLE t; --"' in sql


def test_aggregate_expressions_come_from_a_fixed_map():
    assert casting.aggregate_expression(Aggregation.SUM, "m") == "sum(m)"
    assert casting.aggregate_expression(Aggregation.MEDIAN, "m") == "median(m)"
    assert casting.aggregate_expression(Aggregation.COUNT_DISTINCT, "m") == "count(DISTINCT m)"
    # COUNT is the non-null count of the measure, not count(*).
    assert casting.aggregate_expression(Aggregation.COUNT, "m") == "count(m)"


def test_every_aggregation_has_a_function_mapped():
    """A missing entry would raise a KeyError deep inside SQL generation."""
    for aggregation in Aggregation:
        assert aggregation in casting.AGGREGATE_FUNCTION
