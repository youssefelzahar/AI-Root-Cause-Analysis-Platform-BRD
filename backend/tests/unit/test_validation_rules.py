"""Threshold boundaries are pinned by tests so the PASS/WARNING/BLOCKED rules
cannot drift silently."""

from dataclasses import dataclass, field

from app.analysis.type_inference import ColumnInference
from app.db.models.enums import InferredType as T
from app.services.validation_service import validate_structural


@dataclass
class FakeColumn:
    name: str
    ordinal: int = 0
    null_pct: float = 0.0
    unique_count: int | None = 10
    inference: ColumnInference | None = None
    percentiles: dict = field(default_factory=dict)
    datetime_stats: dict = field(default_factory=dict)


@dataclass
class FakeProfile:
    row_count: int = 100
    column_count: int = 2
    missing_cell_pct: float = 0.0
    duplicate_row_pct: float | None = 0.0
    columns: list = field(default_factory=list)


def _inference(inferred_type=T.NUMERIC, confidence=1.0, requires_conversion=False, invalid=0):
    return ColumnInference(
        column="c",
        raw_type="VARCHAR",
        inferred_type=inferred_type,
        confidence=confidence,
        non_null_count=100,
        null_count=0,
        invalid_value_count=invalid,
        requires_conversion=requires_conversion,
    )


def _healthy_profile(**overrides):
    profile = FakeProfile(
        columns=[
            FakeColumn("revenue", 0, inference=_inference()),
            FakeColumn("date", 1, inference=_inference(T.DATE)),
        ]
    )
    for key, value in overrides.items():
        setattr(profile, key, value)
    return profile


def _codes(profile):
    return {issue.code for issue in validate_structural(profile).issues}


def test_healthy_dataset_passes():
    assert validate_structural(_healthy_profile()).state == "pass"


def test_empty_dataset_is_blocked():
    profile = FakeProfile(row_count=0, columns=[FakeColumn("a", inference=_inference())])
    report = validate_structural(profile)
    assert report.state == "blocked"
    assert "NO_ROWS" in {i.code for i in report.issues}


def test_dataset_without_numeric_column_is_blocked():
    profile = FakeProfile(columns=[FakeColumn("name", inference=_inference(T.STRING))])
    report = validate_structural(profile)
    assert report.state == "blocked"
    assert "NO_NUMERIC_COLUMN" in {i.code for i in report.issues}


def test_missing_time_column_warns_but_does_not_block():
    """Dimension-only analysis is still possible without a date column."""
    profile = FakeProfile(columns=[FakeColumn("revenue", inference=_inference())])
    report = validate_structural(profile)
    assert report.state == "warning"
    assert "NO_TIME_COLUMN" in {i.code for i in report.issues}


def test_lossy_conversion_warns_and_reports_the_detail():
    profile = _healthy_profile()
    profile.columns[0].inference = _inference(confidence=0.95, requires_conversion=True, invalid=5)
    report = validate_structural(profile)
    assert report.state == "warning"
    issue = next(i for i in report.issues if i.code == "LOSSY_TYPE_CONVERSION")
    assert issue.details["invalid_value_count"] == 5
    assert issue.suggested_fix["action"] == "convert"


def test_high_confidence_conversion_is_info_only():
    profile = _healthy_profile()
    profile.columns[0].inference = _inference(confidence=1.0, requires_conversion=True)
    report = validate_structural(profile)
    assert report.state == "pass"
    assert "TYPE_CONVERTED" in {i.code for i in report.issues}


def test_missing_cell_thresholds():
    assert "DATASET_HIGH_MISSING" not in _codes(_healthy_profile(missing_cell_pct=40.0))
    assert "DATASET_HIGH_MISSING" in _codes(_healthy_profile(missing_cell_pct=40.1))
    assert "DATASET_CRITICAL_MISSING" in _codes(_healthy_profile(missing_cell_pct=70.1))
    assert validate_structural(_healthy_profile(missing_cell_pct=70.1)).state == "blocked"


def test_duplicate_row_thresholds():
    assert "HIGH_DUPLICATE_ROWS" not in _codes(_healthy_profile(duplicate_row_pct=30.0))
    assert "HIGH_DUPLICATE_ROWS" in _codes(_healthy_profile(duplicate_row_pct=30.1))
    assert validate_structural(_healthy_profile(duplicate_row_pct=99.5)).state == "blocked"


def test_duplicate_column_names_block():
    profile = FakeProfile(
        columns=[
            FakeColumn("revenue", 0, inference=_inference()),
            FakeColumn("Revenue", 1, inference=_inference()),
            FakeColumn("date", 2, inference=_inference(T.DATE)),
        ]
    )
    report = validate_structural(profile)
    assert report.state == "blocked"
    assert "DUPLICATE_COLUMN_NAMES" in {i.code for i in report.issues}


def test_weak_sample_warns():
    assert "WEAK_SAMPLE" in _codes(_healthy_profile(row_count=10))
    assert "WEAK_SAMPLE" not in _codes(_healthy_profile(row_count=30))
