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


def test_confidence_labels():
    assert confidence_label(1.0) == "high"
    assert confidence_label(0.99) == "high"
    assert confidence_label(0.95) == "medium"
    assert confidence_label(0.85) == "low"
    assert confidence_label(0.5) == "none"
