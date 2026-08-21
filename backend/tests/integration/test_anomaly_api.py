"""POST /api/anomalies/detections, end to end through the real pipeline.

Datasets are created by uploading CSV bytes to the upload endpoint, as every
other integration test does - profiling runs inline because the suite sets
PROFILING_ASYNC=false.
"""

import pytest


def _upload(client, content: bytes, filename: str = "series.csv"):
    return client.post("/api/uploads", files={"file": (filename, content, "text/csv")})


def _define_kpi(client, dataset_id: str, **overrides):
    payload = {
        "name": "Revenue",
        "column": "revenue",
        "aggregation": "SUM",
        "time_column": "order_date",
        "dimensions": ["region"],
        "comparison": "previous_month",
    }
    payload.update(overrides)
    return client.post(f"/api/datasets/{dataset_id}/kpi-definitions", json=payload)


def _detect(client, dataset_id: str, **body):
    return client.post("/api/anomalies/detections", json={"dataset_id": dataset_id, **body})


def _ready_dataset(client, content: bytes, **kpi) -> str:
    upload = _upload(client, content)
    assert upload.status_code == 201, upload.text
    dataset_id = upload.json()["dataset"]["id"]
    defined = _define_kpi(client, dataset_id, **kpi)
    assert defined.status_code == 201, defined.text
    return dataset_id


def _csv(rows: list[tuple[str, float]], region: str = "North") -> bytes:
    lines = ["order_date,region,revenue"]
    lines += [f"{date},{region},{value}" for date, value in rows]
    return ("\n".join(lines) + "\n").encode()


def _months(values: list[float], year: int = 2026) -> list[tuple[str, float]]:
    rows, y, m = [], year, 1
    for value in values:
        rows.append((f"{y}-{m:02d}-15", value))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return rows


# --- the golden path ----------------------------------------------------------


def test_a_detection_finds_the_period_that_collapsed(client, anomaly_golden_csv_bytes) -> None:
    """Six steady months and one at 600. Nothing tells the engine which."""
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    response = _detect(client, dataset_id)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["status"] == "OK"
    assert len(body["anomalies"]) == 1

    found = body["anomalies"][0]
    assert found["value"] == pytest.approx(600.0)
    assert found["period_start"].startswith("2026-07")
    assert found["severity"] == "CRITICAL"
    assert found["direction"] == "DOWNWARD"
    assert found["is_anomaly"] is True


def test_a_detection_reports_the_expected_value_and_both_deviations(
    client, anomaly_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    found = _detect(client, dataset_id).json()["anomalies"][0]

    assert found["baseline"]["expected_value"] == pytest.approx(1002.5)
    assert found["absolute_deviation"] == pytest.approx(-402.5)
    assert found["percentage_deviation"] == pytest.approx(-40.15, abs=0.01)
    assert found["anomaly_score"] == pytest.approx(-27.1486, abs=1e-4)


def test_a_detection_returns_the_whole_series_not_only_the_anomalies(
    client, anomaly_golden_csv_bytes
) -> None:
    """The chart needs every period; the table needs only the flagged ones."""
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    body = _detect(client, dataset_id).json()
    assert len(body["series"]) == 7
    assert [p["value"] for p in body["series"]][-1] == pytest.approx(600.0)


def test_a_detection_points_at_the_latest_evaluated_period(
    client, anomaly_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    body = _detect(client, dataset_id).json()
    assert body["latest"]["period_start"].startswith("2026-07")
    assert body["latest"]["is_anomaly"] is True


def test_periods_without_enough_history_are_distinguished_from_normal_ones(
    client, anomaly_golden_csv_bytes
) -> None:
    """Five of seven periods cannot be judged. Reporting them as normal would
    claim six clean months where there is evidence for one."""
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    statuses = [p["status"] for p in _detect(client, dataset_id).json()["series"]]
    assert statuses[:5] == ["INSUFFICIENT_HISTORY"] * 5
    assert statuses[5:] == ["EVALUATED", "EVALUATED"]


def test_a_detection_names_the_method_and_thresholds_it_used(
    client, anomaly_golden_csv_bytes
) -> None:
    """A threshold the reader cannot see is one they cannot argue with."""
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    method = _detect(client, dataset_id).json()["method"]
    assert method["name"] == "robust_zscore"
    assert method["anomaly_threshold"] == pytest.approx(3.5)
    assert method["severity_thresholds"]["CRITICAL"] == pytest.approx(12.0)
    assert "modified z-score" in method["score_interpretation"].lower()


def test_a_detection_reports_the_rows_it_was_built_from(
    client, anomaly_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    evidence = _detect(client, dataset_id).json()["evidence"]
    assert evidence["total_rows"] == 7
    assert evidence["periods_observed"] == 7
    assert evidence["periods_evaluated"] == 2
    assert evidence["unparsed_time_rows"] == 0
    assert evidence["statements_executed"] > 0


def test_a_detection_states_what_the_method_cannot_see(
    client, anomaly_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    limitations = _detect(client, dataset_id).json()["limitations"]
    assert limitations
    assert any("seasonal" in text.lower() for text in limitations)


def test_the_summary_describes_the_deviation_without_claiming_a_cause(
    client, anomaly_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    summary = _detect(client, dataset_id).json()["summary"]
    assert "baseline" in summary.lower()
    assert "caused" not in summary.lower()


# --- no anomaly ---------------------------------------------------------------


def test_a_steady_kpi_reports_no_anomalies(client) -> None:
    dataset_id = _ready_dataset(client, _csv(_months([1000, 1020, 980, 1010, 990, 1005, 995])))
    body = _detect(client, dataset_id).json()
    assert body["status"] == "OK"
    assert body["anomalies"] == []
    assert body["latest"]["severity"] == "NORMAL"
    assert "normal range" in body["summary"]


# --- degenerate input ---------------------------------------------------------


def test_the_shortest_analysable_history_still_scores_nothing(client) -> None:
    """Two periods is the fewest a KPI can be configured on at all, and it is
    still far too few to judge one against the other."""
    dataset_id = _ready_dataset(client, _csv(_months([1000, 1010])))
    body = _detect(client, dataset_id).json()
    assert body["status"] == "INSUFFICIENT_HISTORY"
    assert body["anomalies"] == []
    assert body["latest"] is None
    assert any(n["code"] == "INSUFFICIENT_HISTORY" for n in body["notices"])


def test_a_history_shorter_than_the_minimum_baseline_scores_nothing(client) -> None:
    dataset_id = _ready_dataset(client, _csv(_months([1000, 1010, 990, 1005])))
    body = _detect(client, dataset_id).json()
    assert body["status"] == "INSUFFICIENT_HISTORY"
    assert body["evidence"]["periods_evaluated"] == 0


def test_a_constant_kpi_reports_no_anomalies(client) -> None:
    dataset_id = _ready_dataset(client, _csv(_months([500] * 12)))
    body = _detect(client, dataset_id).json()
    assert body["status"] == "OK"
    assert body["anomalies"] == []


# --- the series ---------------------------------------------------------------


def test_missing_periods_are_gaps_in_the_series_rather_than_zeros(client) -> None:
    """A month with no rows is not a month of zero revenue, and a chart that
    draws it at zero invents a collapse that never happened."""
    rows = _months([1000, 1010, 990, 1005, 995, 1000])
    del rows[3]  # April has no rows at all
    dataset_id = _ready_dataset(client, _csv(rows))
    body = _detect(client, dataset_id).json()

    april = [p for p in body["series"] if p["period_start"].startswith("2026-04")]
    assert len(april) == 1
    assert april[0]["value"] is None
    assert april[0]["status"] == "MISSING"
    assert april[0]["anomaly_score"] is None
    assert body["evidence"]["periods_missing"] == 1
    assert any(n["code"] == "MISSING_PERIODS" for n in body["notices"])


def test_several_rows_in_one_period_are_aggregated_into_a_single_point(client) -> None:
    rows = [("2026-01-05", 400.0), ("2026-01-20", 600.0), ("2026-02-15", 1000.0)]
    dataset_id = _ready_dataset(client, _csv(rows))
    body = _detect(client, dataset_id).json()
    january = [p for p in body["series"] if p["period_start"].startswith("2026-01")]
    assert len(january) == 1
    assert january[0]["value"] == pytest.approx(1000.0)
    assert january[0]["row_count"] == 2


def test_rows_with_an_unreadable_date_are_counted_and_reported(client) -> None:
    """One bad row among many: the series is still built, and the row is
    accounted for rather than silently vanishing from the totals."""
    rows = _months([1000.0] * 24) + _months([1000.0] * 24)
    content = _csv(rows) + b"not-a-date,South,900\n"
    dataset_id = _ready_dataset(client, content)
    body = _detect(client, dataset_id).json()
    assert body["evidence"]["unparsed_time_rows"] == 1
    assert any(n["code"] == "UNPARSED_TIME_ROWS" for n in body["notices"])


def test_a_null_measure_is_not_counted_as_a_zero(client) -> None:
    """SUM over {1000, NULL} is 1000. Treating the null as a zero would halve
    an AVG and invent a drop that the data does not contain."""
    rows = _months([1000.0] * 12)
    lines = ["order_date,region,revenue"]
    lines += [f"{date},North,{value}" for date, value in rows]
    lines.append("2026-01-20,South,")  # a second January row with no measure
    dataset_id = _ready_dataset(client, ("\n".join(lines) + "\n").encode())

    body = _detect(client, dataset_id).json()
    january = body["series"][0]
    assert january["value"] == pytest.approx(1000.0)
    assert january["row_count"] == 2


# --- aggregations -------------------------------------------------------------


@pytest.mark.parametrize(
    "aggregation", ["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX", "MEDIAN"]
)
def test_every_aggregation_produces_a_series(client, aggregation) -> None:
    """Unlike RCA, no aggregation is unattributable here: a series has no
    decomposition to be non-additive about, so MEDIAN and COUNT_DISTINCT are
    every bit as valid as SUM."""
    dataset_id = _ready_dataset(
        client, _csv(_months([1000, 1020, 980, 1010, 990, 1005, 600])), aggregation=aggregation
    )
    response = _detect(client, dataset_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kpi"]["aggregation"] == aggregation
    assert len(body["series"]) == 7


def test_a_count_series_counts_rows_per_period(client) -> None:
    rows = [("2026-01-05", 1.0), ("2026-01-20", 1.0), ("2026-02-15", 1.0)]
    dataset_id = _ready_dataset(client, _csv(rows), aggregation="COUNT")
    body = _detect(client, dataset_id).json()
    assert body["series"][0]["value"] == pytest.approx(2.0)
    assert body["series"][1]["value"] == pytest.approx(1.0)


# --- the grain ----------------------------------------------------------------


def test_an_explicit_grain_overrides_the_profiled_frequency(client) -> None:
    dataset_id = _ready_dataset(client, _csv(_months([1000, 1010, 990, 1005, 995, 1000])))
    body = _detect(client, dataset_id, grain="year").json()
    assert body["kpi"]["grain"] == "year"
    assert len(body["series"]) == 1


def test_an_unknown_frequency_is_reported_as_assumed_rather_than_guessed(client) -> None:
    dataset_id = _ready_dataset(client, _csv(_months([1000, 1010, 990, 1005, 995, 1000])))
    body = _detect(client, dataset_id).json()
    if any(n["code"] == "GRAIN_ASSUMED" for n in body["notices"]):
        assert body["kpi"]["grain"] == "month"


def test_a_grain_a_series_cannot_be_built_on_is_rejected(client) -> None:
    dataset_id = _ready_dataset(client, _csv(_months([1000, 1010, 990])))
    response = _detect(client, dataset_id, grain="equal_span")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ANOMALY_GRAIN_UNSUPPORTED"


def test_a_nonsense_grain_is_rejected(client) -> None:
    dataset_id = _ready_dataset(client, _csv(_months([1000, 1010, 990])))
    response = _detect(client, dataset_id, grain="fortnight")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ANOMALY_GRAIN_UNSUPPORTED"


# --- the method ---------------------------------------------------------------


def test_the_iqr_method_can_be_asked_for_by_name(client, anomaly_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    body = _detect(client, dataset_id, method="iqr").json()
    assert body["method"]["name"] == "iqr"
    assert len(body["anomalies"]) == 1


def test_an_unimplemented_method_is_refused_rather_than_silently_substituted(
    client, anomaly_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    response = _detect(client, dataset_id, method="seasonal")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ANOMALY_METHOD_UNSUPPORTED"


# --- gates --------------------------------------------------------------------


def test_a_kpi_without_a_time_column_is_rejected(client, anomaly_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes, time_column=None)
    response = _detect(client, dataset_id)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "KPI_TIME_COLUMN_REQUIRED"


def test_a_detection_requires_an_analysis_ready_dataset(
    client, anomaly_golden_csv_bytes
) -> None:
    upload = _upload(client, anomaly_golden_csv_bytes)
    dataset_id = upload.json()["dataset"]["id"]  # profiled, but no KPI yet
    response = _detect(client, dataset_id)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_ANALYSIS_READY"


def test_a_missing_dataset_returns_not_found(client) -> None:
    response = _detect(client, "00000000-0000-0000-0000-0000000000ff")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_a_kpi_definition_from_another_dataset_is_rejected(
    client, anomaly_golden_csv_bytes
) -> None:
    first = _ready_dataset(client, anomaly_golden_csv_bytes)
    second = _ready_dataset(client, anomaly_golden_csv_bytes)
    other = client.get(f"/api/datasets/{second}/kpi-definitions/active").json()

    response = _detect(client, first, kpi_definition_id=other["id"])
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "KPI_DEFINITION_NOT_FOUND"


def test_another_company_cannot_run_a_detection_on_this_dataset(
    client, anomaly_golden_csv_bytes, other_company
) -> None:
    """Cross-tenant access is a 404, not a 403 - existence is not disclosed."""
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    response = client.post(
        "/api/anomalies/detections",
        json={"dataset_id": dataset_id},
        headers={"X-Company-Id": str(other_company)},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_an_out_of_range_baseline_window_is_refused(client, anomaly_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, anomaly_golden_csv_bytes)
    assert _detect(client, dataset_id, baseline_window=2).status_code == 422
    assert _detect(client, dataset_id, baseline_window=500).status_code == 422
