"""The engine driven against real DuckDB relations.

Still no database and no HTTP client: these call ``run_investigation`` directly
against a CSV on disk, which is where the physical-typing behaviour and the
query shape can be observed.
"""

import pytest

from app.analysis.duckdb_session import duckdb_connection, source_relation
from app.analysis.rca import run_investigation
from app.analysis.rca.models import AnalysisState, AttributionBasis, RcaSpec
from app.core.exceptions import ValidationError
from app.db.models.enums import Aggregation, ComparisonPeriod

GOLDEN = """order_date,region,product,segment,revenue
2026-06-15,Cairo,A,Enterprise,500
2026-06-15,Cairo,B,SMB,300
2026-06-15,Giza,A,Enterprise,400
2026-06-15,Giza,B,SMB,300
2026-07-15,Cairo,A,Enterprise,200
2026-07-15,Cairo,B,SMB,300
2026-07-15,Giza,A,Enterprise,400
2026-07-15,Giza,B,SMB,300
"""

# Same numbers, but formatted so DuckDB types every column as VARCHAR - the
# shape excel_to_parquet and rows_to_parquet produce.
MESSY = """order_date,region,product,segment,revenue
15-Jun-2026,Cairo,A,Enterprise,"$500.00"
15-Jun-2026,Cairo,B,SMB,"$300.00"
15-Jun-2026,Giza,A,Enterprise,"$400.00"
15-Jun-2026,Giza,B,SMB,"$300.00"
15-Jul-2026,Cairo,A,Enterprise,"$200.00"
15-Jul-2026,Cairo,B,SMB,"$300.00"
15-Jul-2026,Giza,A,Enterprise,"$400.00"
15-Jul-2026,Giza,B,SMB,"$300.00"
"""


def _write(tmp_path, content: str, name: str = "data.csv"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _spec(**overrides) -> RcaSpec:
    defaults = dict(
        kpi_name="Revenue",
        measure_column="revenue",
        aggregation=Aggregation.SUM,
        time_column="order_date",
        dimensions=("region", "product", "segment"),
        comparison=ComparisonPeriod.PREVIOUS_MONTH,
        detected_frequency="monthly",
    )
    defaults.update(overrides)
    return RcaSpec(**defaults)


def _run(path, spec: RcaSpec):
    with duckdb_connection() as conn:
        return run_investigation(conn, source_relation(path, "csv"), spec)


def _physical(path) -> dict[str, str]:
    with duckdb_connection() as conn:
        relation = source_relation(path, "csv")
        rows = conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    return {row[0]: str(row[1]) for row in rows}


# --- the golden example -------------------------------------------------------


def test_the_golden_dataset_finds_the_expected_driver_chain(tmp_path):
    """PRD section 19: only Cairo / A / Enterprise moves, so the engine must
    discover that chain from the arithmetic alone."""
    result = _run(_write(tmp_path, GOLDEN), _spec())

    assert result.state is AnalysisState.OK
    assert result.kpi.previous_value == pytest.approx(1500.0)
    assert result.kpi.current_value == pytest.approx(1200.0)
    assert result.kpi.absolute_change == pytest.approx(-300.0)
    assert result.kpi.percent_change == pytest.approx(-20.0)

    assert [d.value for d in result.primary_drivers] == ["Cairo"]
    assert result.primary_drivers[0].contribution == pytest.approx(1.0)

    node = result.tree
    assert node.child_dimension == "region"
    cairo = next(c for c in node.children if c.value == "Cairo")
    product = next(c for c in cairo.children if c.value == "A")
    segment = product.children[0]
    assert [n.value for n in (cairo, product, segment)] == ["Cairo", "A", "Enterprise"]
    assert segment.value == "Enterprise"
    assert segment.contribution == pytest.approx(1.0)
    # The last level has only one value, which is a finding but cannot be scored
    # by deviation-from-proportional.
    assert segment.is_pure_split


def test_explanatory_power_breaks_a_tie_on_principle(tmp_path):
    """All three dimensions contribute 1.0, so contribution cannot choose.

    Cairo's collapse is a bigger surprise relative to its 53.3% baseline share
    than Product A's is relative to its 60% share, so region wins.
    """
    result = _run(_write(tmp_path, GOLDEN), _spec())
    power = {s.dimension: s.explanatory_power for s in result.dimensions_analysed}
    assert power["region"] == pytest.approx(0.9333, abs=1e-4)
    assert power["product"] == pytest.approx(0.8)
    assert power["segment"] == pytest.approx(0.8)
    assert result.tree.child_dimension == "region"


def test_contributions_sum_to_one_and_are_reported(tmp_path):
    result = _run(_write(tmp_path, GOLDEN), _spec())
    assert result.evidence.contribution_sum == pytest.approx(1.0)


# --- the typing trap ----------------------------------------------------------


def test_a_text_only_dataset_yields_the_same_numbers_as_a_typed_one(tmp_path):
    """The strongest guard against the Excel / SQL Server regression.

    Both files hold the same values; only the physical types differ.
    """
    typed = _physical(_write(tmp_path, GOLDEN, "typed.csv"))
    text = _physical(_write(tmp_path, MESSY, "text.csv"))
    assert typed["revenue"] == "BIGINT" and typed["order_date"] == "DATE"
    assert text["revenue"] == "VARCHAR" and text["order_date"] == "VARCHAR"

    a = _run(tmp_path / "typed.csv", _spec())
    b = _run(tmp_path / "text.csv", _spec())

    assert b.kpi.previous_value == pytest.approx(a.kpi.previous_value)
    assert b.kpi.current_value == pytest.approx(a.kpi.current_value)
    assert b.kpi.absolute_change == pytest.approx(a.kpi.absolute_change)
    assert [d.value for d in b.primary_drivers] == [d.value for d in a.primary_drivers]
    assert b.primary_drivers[0].contribution == pytest.approx(
        a.primary_drivers[0].contribution
    )


def test_currency_formatted_text_is_summed_not_dropped(tmp_path):
    result = _run(_write(tmp_path, MESSY), _spec())
    assert result.kpi.previous_value == pytest.approx(1500.0)
    assert result.evidence.unparsed_measure_rows == 0


def test_day_first_dates_stored_as_text_are_parsed(tmp_path):
    result = _run(_write(tmp_path, MESSY), _spec())
    assert result.evidence.unparsed_time_rows == 0
    assert result.periods is not None


# --- query shape --------------------------------------------------------------


def test_the_statement_count_does_not_grow_with_the_dimension_count(tmp_path):
    """Guards against accidental per-dimension or per-node recursion.

    Level 1 is one statement for all dimensions, and each tree level is one
    more - not one per dimension per level.
    """
    path = _write(tmp_path, GOLDEN)
    one = _run(path, _spec(dimensions=("region",)))
    three = _run(path, _spec(dimensions=("region", "product", "segment")))
    assert three.evidence.statements_executed - one.evidence.statements_executed <= 3
    assert three.evidence.statements_executed <= 10


# --- segment lifecycle --------------------------------------------------------


def test_a_segment_present_in_only_one_period_still_appears(tmp_path):
    """A join would drop these; they are usually the most interesting rows."""
    content = """order_date,region,revenue
2026-06-15,Cairo,1000
2026-06-15,Luxor,250
2026-07-15,Cairo,1000
2026-07-15,Aswan,100
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    segments = {
        node.value: node for _, nodes in result.dimension_results for node in nodes
    }
    assert segments["Luxor"].is_lost_segment
    assert segments["Aswan"].is_new_segment
    assert segments["Luxor"].absolute_change == pytest.approx(-250.0)
    assert segments["Aswan"].absolute_change == pytest.approx(100.0)


def test_rows_with_a_null_dimension_form_their_own_segment(tmp_path):
    """Dropping them would break the invariant that segments sum to the total."""
    content = """order_date,region,revenue
2026-06-15,Cairo,1000
2026-06-15,,500
2026-07-15,Cairo,900
2026-07-15,,300
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    nodes = [node for _, group in result.dimension_results for node in group]
    assert any(node.value_is_null for node in nodes)
    assert result.evidence.contribution_sum == pytest.approx(1.0)


# --- aggregations -------------------------------------------------------------


def test_a_median_kpi_reports_changes_but_withholds_contributions(tmp_path):
    """A median is not decomposable, and inventing a formula would be worse than
    saying so."""
    result = _run(_write(tmp_path, GOLDEN), _spec(aggregation=Aggregation.MEDIAN))
    assert result.state is AnalysisState.UNATTRIBUTABLE
    assert result.attribution.basis is AttributionBasis.UNATTRIBUTABLE
    assert result.primary_drivers == ()
    assert result.tree is None
    nodes = [node for _, group in result.dimension_results for node in group]
    assert nodes, "segment level numbers must still be reported"
    assert all(node.contribution is None for node in nodes)
    assert all(node.absolute_change is not None for node in nodes)
    assert any(n.code == "AGGREGATION_NOT_ATTRIBUTABLE" for n in result.notices)


def test_an_average_kpi_reports_rate_and_mix_effects(tmp_path):
    result = _run(_write(tmp_path, GOLDEN), _spec(aggregation=Aggregation.AVG))
    assert result.attribution.basis is AttributionBasis.MIX_RATE
    nodes = [node for _, group in result.dimension_results for node in group]
    assert any(node.rate_effect is not None for node in nodes)
    assert any(node.mix_effect is not None for node in nodes)


def test_a_count_kpi_works_on_a_non_numeric_measure(tmp_path):
    content = """order_date,region,customer
2026-06-15,Cairo,acme
2026-06-15,Cairo,globex
2026-07-15,Cairo,acme
"""
    result = _run(
        _write(tmp_path, content),
        _spec(measure_column="customer", aggregation=Aggregation.COUNT, dimensions=("region",)),
    )
    assert result.kpi.previous_value == pytest.approx(2.0)
    assert result.kpi.current_value == pytest.approx(1.0)


# --- error and empty states ---------------------------------------------------


def test_a_missing_column_is_reported_by_name(tmp_path):
    """Doubles as schema-drift detection: the definition is a stored row and the
    file can be re-uploaded with a different shape."""
    with pytest.raises(ValidationError) as excinfo:
        _run(_write(tmp_path, GOLDEN), _spec(measure_column="ghost"))
    assert excinfo.value.code == "RCA_COLUMN_MISSING"
    assert excinfo.value.details["column"] == "ghost"


def test_a_non_numeric_measure_is_rejected_for_a_sum(tmp_path):
    content = """order_date,region,revenue
2026-06-15,Cairo,alpha
2026-07-15,Cairo,beta
"""
    with pytest.raises(ValidationError) as excinfo:
        _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert excinfo.value.code == "RCA_MEASURE_NOT_NUMERIC"


def test_a_single_period_dataset_reports_no_previous_period(tmp_path):
    content = """order_date,region,revenue
2026-07-15,Cairo,1000
2026-07-16,Giza,500
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert result.state is AnalysisState.NO_PREVIOUS_PERIOD
    assert result.primary_drivers == ()


def test_a_kpi_that_did_not_change_says_so_rather_than_naming_drivers(tmp_path):
    content = """order_date,region,revenue
2026-06-15,Cairo,1000
2026-07-15,Cairo,1000
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert result.state is AnalysisState.NO_CHANGE
    assert result.primary_drivers == ()
    assert any(n.code == "NO_CHANGE_DETECTED" for n in result.notices)


def test_a_negative_kpi_getting_worse_reports_a_negative_percentage(tmp_path):
    """-100 to -150 is a 50% deterioration, not a 50% improvement."""
    content = """order_date,region,revenue
2026-06-15,Cairo,-100
2026-07-15,Cairo,-150
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert result.kpi.absolute_change == pytest.approx(-50.0)
    assert result.kpi.percent_change == pytest.approx(-50.0)


def test_a_zero_baseline_gives_an_undefined_percentage_not_infinity(tmp_path):
    content = """order_date,region,revenue
2026-06-15,Cairo,0
2026-07-15,Cairo,500
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert result.kpi.percent_change is None
    assert result.kpi.percent_change_undefined_reason == "zero_baseline"


def test_large_cancelling_movements_switch_to_shares_of_gross_movement(tmp_path):
    """The net barely moved, so dividing by it would report thousands of percent."""
    content = """order_date,region,revenue
2026-06-15,Cairo,1000
2026-06-15,Giza,1000
2026-07-15,Cairo,1990
2026-07-15,Giza,20
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert result.attribution.basis is AttributionBasis.GROSS_MOVEMENT
    nodes = [node for _, group in result.dimension_results for node in group]
    assert sum(abs(node.contribution) for node in nodes) == pytest.approx(1.0)


def test_a_proportional_change_is_reported_as_broad_based(tmp_path):
    """Every region halved, so no region explains it more than any other."""
    content = """order_date,region,revenue
2026-06-15,Cairo,400
2026-06-15,Giza,400
2026-06-15,Luxor,400
2026-06-15,Aswan,400
2026-07-15,Cairo,200
2026-07-15,Giza,200
2026-07-15,Luxor,200
2026-07-15,Aswan,200
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert result.attribution.change_pattern.value == "broad_based"
    assert result.primary_drivers == ()
    assert result.tree is None


def test_a_kpi_with_no_dimensions_still_reports_the_change(tmp_path):
    result = _run(_write(tmp_path, GOLDEN), _spec(dimensions=()))
    assert result.kpi.absolute_change == pytest.approx(-300.0)
    assert result.primary_drivers == ()
    assert any(n.code == "NO_DIMENSIONS_CONFIGURED" for n in result.notices)


def test_a_kpi_without_a_time_column_cannot_be_compared(tmp_path):
    result = _run(_write(tmp_path, GOLDEN), _spec(time_column=None))
    assert result.state is AnalysisState.NO_TIME_COLUMN
    assert result.periods is None


def test_a_high_cardinality_dimension_is_truncated_with_a_notice(tmp_path):
    rows = ["order_date,region,revenue"]
    for i in range(80):
        rows.append(f"2026-06-15,r{i},{100 + i}")
        rows.append(f"2026-07-15,r{i},{90 + i}")
    result = _run(
        _write(tmp_path, "\n".join(rows) + "\n"),
        _spec(dimensions=("region",), max_values_per_dimension=50),
    )
    assert any(n.code == "DIMENSION_TRUNCATED" for n in result.notices)
    summary = next(s for s in result.dimensions_analysed if s.dimension == "region")
    assert summary.truncated
    nodes = [node for _, group in result.dimension_results for node in group]
    # The remainder is folded into an exact residual, so the total still holds.
    assert any(node.is_other_bucket for node in nodes)
    assert result.evidence.contribution_sum == pytest.approx(1.0)


def test_equal_contributors_are_all_reported(tmp_path):
    """Two regions moved by exactly the same amount; neither outranks the other."""
    content = """order_date,region,revenue
2026-06-15,Cairo,1000
2026-06-15,Giza,1000
2026-07-15,Cairo,700
2026-07-15,Giza,700
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    contributions = {
        node.value: node.contribution
        for _, group in result.dimension_results
        for node in group
    }
    assert contributions["Cairo"] == pytest.approx(0.5)
    assert contributions["Giza"] == pytest.approx(0.5)


def test_a_single_row_dataset_does_not_crash(tmp_path):
    content = """order_date,region,revenue
2026-07-15,Cairo,1000
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert result.state is AnalysisState.NO_PREVIOUS_PERIOD


def test_a_header_only_dataset_reports_no_data(tmp_path):
    content = "order_date,region,revenue\n"
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert result.state is AnalysisState.NO_DATA
    assert result.primary_drivers == ()


def test_rows_outside_both_periods_are_counted_not_silently_ignored(tmp_path):
    content = """order_date,region,revenue
2026-01-15,Cairo,9999
2026-06-15,Cairo,1000
2026-07-15,Cairo,800
"""
    result = _run(_write(tmp_path, content), _spec(dimensions=("region",)))
    assert result.evidence.rows_outside_periods == 1
    assert result.kpi.previous_value == pytest.approx(1000.0)
