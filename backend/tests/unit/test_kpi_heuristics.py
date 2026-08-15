from app.analysis.kpi_heuristics import ColumnFacts, detect, is_identifier, recommended_default
from app.db.models.enums import InferredType as T


def _prd_columns():
    """The exact example dataset from PRD section 11."""
    return [
        ColumnFacts("revenue", T.NUMERIC, row_count=1000, unique_count=900, min_value=10, max_value=9999),
        ColumnFacts("cost", T.NUMERIC, row_count=1000, unique_count=850, min_value=5, max_value=5000),
        ColumnFacts("profit", T.NUMERIC, row_count=1000, unique_count=800, min_value=-100, max_value=4000),
        ColumnFacts("quantity", T.INTEGER, row_count=1000, unique_count=50, min_value=1, max_value=99),
        ColumnFacts("date", T.DATE, row_count=1000, unique_count=365, distinct_periods=365),
        ColumnFacts("region", T.STRING, row_count=1000, unique_count=5),
        ColumnFacts("product", T.STRING, row_count=1000, unique_count=20),
        ColumnFacts("customer", T.STRING, row_count=1000, unique_count=200),
    ]


def test_prd_example_produces_the_documented_recommendations():
    result = detect(_prd_columns())
    assert {"revenue", "profit", "quantity"} <= {c.column for c in result.measures}
    assert [c.column for c in result.time_columns] == ["date"]
    assert {"region", "product", "customer"} <= {c.column for c in result.dimensions}


def test_recommended_default_picks_the_prd_dimensions():
    default = recommended_default(detect(_prd_columns()), "daily")
    assert default["column"] == "revenue"
    assert default["time_column"] == "date"
    assert default["dimensions"] == ["region", "product", "customer"]
    assert default["aggregation"] == "SUM"


def test_identifiers_are_never_offered_as_measures():
    columns = _prd_columns() + [
        ColumnFacts("order_id", T.INTEGER, row_count=1000, unique_count=1000, min_value=1, max_value=1000),
        ColumnFacts("customer_id", T.INTEGER, row_count=1000, unique_count=980, min_value=1, max_value=999),
    ]
    measures = {c.column for c in detect(columns).measures}
    assert "order_id" not in measures
    assert "customer_id" not in measures


def test_is_identifier_detects_key_like_columns():
    assert is_identifier(ColumnFacts("id", T.INTEGER, row_count=100, unique_count=100))
    assert is_identifier(ColumnFacts("product_code", T.STRING, row_count=100, unique_count=90))
    assert not is_identifier(ColumnFacts("revenue", T.NUMERIC, row_count=100, unique_count=40))


def test_rate_columns_suggest_avg_not_sum():
    columns = [
        ColumnFacts(
            "conversion_rate", T.NUMERIC, row_count=1000, unique_count=400, min_value=0.0, max_value=1.0
        )
    ]
    assert detect(columns).measures[0].suggested_aggregation == "AVG"


def test_high_cardinality_text_is_not_a_dimension():
    assert detect([ColumnFacts("free_text", T.STRING, row_count=1000, unique_count=995)]).dimensions == []


def test_year_integer_column_is_accepted_as_a_time_candidate():
    columns = [
        ColumnFacts(
            "year", T.INTEGER, row_count=100, unique_count=5,
            min_value=2020, max_value=2026, distinct_periods=5,
        )
    ]
    assert [c.column for c in detect(columns).time_columns] == ["year"]


def test_every_candidate_explains_itself():
    """PRD principle 6: analytical claims must be traceable."""
    result = detect(_prd_columns())
    for group in (result.measures, result.dimensions, result.time_columns):
        assert all(candidate.reasons for candidate in group)
