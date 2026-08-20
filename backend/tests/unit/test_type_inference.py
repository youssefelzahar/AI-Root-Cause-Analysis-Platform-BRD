from app.analysis.type_inference import confidence_label, resolve_type
from app.db.models.enums import InferredType as T


def test_clean_numeric_column_is_high_confidence():
    result = resolve_type(
        column="revenue",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={T.NUMERIC: 100, T.INTEGER: 100},
    )
    assert result.inferred_type == T.INTEGER
    assert result.confidence == 1.0
    assert result.confidence_label == "high"
    assert result.invalid_value_count == 0


def test_text_column_that_needs_cleaning_is_penalised_but_accepted():
    """PRD section 10: attempt safe conversion rather than reject."""
    result = resolve_type(
        column="revenue",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={T.NUMERIC: 10},
        cleaned_castable={T.NUMERIC: 100},
    )
    assert result.inferred_type == T.NUMERIC
    assert result.used_cleaning is True
    assert result.requires_conversion is True
    assert result.confidence == 0.95


def test_partially_invalid_column_reports_the_bad_values():
    result = resolve_type(
        column="revenue",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={T.NUMERIC: 95},
    )
    assert result.inferred_type == T.NUMERIC
    assert result.invalid_value_count == 5
    assert result.confidence_label == "medium"


def test_column_below_the_floor_stays_text():
    result = resolve_type(
        column="notes",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={T.NUMERIC: 50},
    )
    assert result.inferred_type == T.STRING


def test_leading_zero_codes_are_never_converted_to_numbers():
    """Converting a postcode to an integer would destroy data."""
    result = resolve_type(
        column="postcode",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={T.INTEGER: 100, T.NUMERIC: 100},
        has_leading_zeros=True,
    )
    assert result.inferred_type == T.STRING


def test_multi_valued_column_is_not_treated_as_boolean():
    result = resolve_type(
        column="rating",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={T.BOOLEAN: 100, T.INTEGER: 100},
        distinct_count=5,
    )
    assert result.inferred_type == T.INTEGER


def test_all_null_column_is_reported_not_crashed():
    result = resolve_type(
        column="empty", raw_type="VARCHAR", non_null_count=0, null_count=50, raw_castable={}
    )
    assert result.inferred_type == T.STRING
    assert result.confidence == 0.0


def test_day_first_dates_beat_the_integer_left_by_stripping_the_slashes():
    """A '30/01/2013' column must not become the integer 30012013.

    DuckDB's native DATE cast only reads ISO ordering, so the raw counts are
    zero and the numeric cleaner - which strips '/' - is the only other reading
    that parses the whole column.
    """
    result = resolve_type(
        column="Review Date",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={},
        cleaned_castable={T.INTEGER: 100, T.NUMERIC: 100},
        format_castable={"%d/%m/%Y": 100},
    )
    assert result.inferred_type == T.DATE
    assert result.date_format == "%d/%m/%Y"
    assert result.confidence == 1.0
    assert result.requires_conversion is True
    assert result.day_month_ambiguous is False


def test_a_genuine_number_column_is_not_stolen_by_the_date_branch():
    result = resolve_type(
        column="revenue",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={T.INTEGER: 100, T.NUMERIC: 100},
        format_castable={},
    )
    assert result.inferred_type == T.INTEGER
    assert result.date_format is None


def test_iso_dates_keep_the_native_cast_rather_than_a_format():
    result = resolve_type(
        column="order_date",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={T.DATE: 100},
        format_castable={"%Y-%m-%d": 100},
    )
    assert result.inferred_type == T.DATE
    assert result.date_format is None


def test_datetime_format_resolves_to_datetime_not_date():
    result = resolve_type(
        column="created",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={},
        format_castable={"%d/%m/%Y %H:%M:%S": 100},
    )
    assert result.inferred_type == T.DATETIME
    assert result.date_format == "%d/%m/%Y %H:%M:%S"


def test_wholly_ambiguous_day_month_column_is_flagged():
    """Every day-of-month <= 12, so both orderings parse and one is a guess."""
    result = resolve_type(
        column="Review Date",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={},
        format_castable={"%d/%m/%Y": 100, "%m/%d/%Y": 100},
    )
    assert result.inferred_type == T.DATE
    assert result.day_month_ambiguous is True


def test_a_mostly_broken_date_column_still_loses_to_a_clean_number():
    """The temporal override only applies above the acceptance floor."""
    result = resolve_type(
        column="mixed",
        raw_type="VARCHAR",
        non_null_count=100,
        null_count=0,
        raw_castable={},
        cleaned_castable={T.INTEGER: 100},
        format_castable={"%d/%m/%Y": 40},
    )
    assert result.inferred_type == T.INTEGER


def test_confidence_labels():
    assert confidence_label(1.0) == "high"
    assert confidence_label(0.99) == "high"
    assert confidence_label(0.95) == "medium"
    assert confidence_label(0.85) == "low"
    assert confidence_label(0.5) == "none"
