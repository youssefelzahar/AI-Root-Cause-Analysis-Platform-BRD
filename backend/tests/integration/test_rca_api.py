"""POST /api/rca/investigations, end to end through the real pipeline.

Datasets are created by uploading CSV bytes to the upload endpoint, as every
other integration test does - profiling runs inline because the suite sets
PROFILING_ASYNC=false.
"""

import pytest


def _upload(client, content: bytes, filename: str = "sales.csv"):
    return client.post("/api/uploads", files={"file": (filename, content, "text/csv")})


def _define_kpi(client, dataset_id: str, **overrides):
    payload = {
        "name": "Revenue",
        "column": "revenue",
        "aggregation": "SUM",
        "time_column": "order_date",
        "dimensions": ["region", "product", "segment"],
        "comparison": "previous_month",
    }
    payload.update(overrides)
    return client.post(f"/api/datasets/{dataset_id}/kpi-definitions", json=payload)


def _investigate(client, dataset_id: str, **body):
    return client.post(
        "/api/rca/investigations", json={"dataset_id": dataset_id, **body}
    )


def _ready_dataset(client, content: bytes, **kpi) -> str:
    upload = _upload(client, content)
    assert upload.status_code == 201, upload.text
    dataset_id = upload.json()["dataset"]["id"]
    defined = _define_kpi(client, dataset_id, **kpi)
    assert defined.status_code == 201, defined.text
    return dataset_id


# --- the golden path ----------------------------------------------------------


def test_an_investigation_explains_the_change_between_the_last_two_periods(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    response = _investigate(client, dataset_id)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["state"] == "ok"
    kpi = body["kpi"]
    assert kpi["previous_value"] == pytest.approx(1500.0)
    assert kpi["current_value"] == pytest.approx(1200.0)
    assert kpi["absolute_change"] == pytest.approx(-300.0)
    assert kpi["percent_change"] == pytest.approx(-20.0)
    assert kpi["direction"] == "down"
    assert kpi["severity"] == "high"


def test_an_investigation_names_the_periods_it_compared(client, rca_golden_csv_bytes) -> None:
    """Which two windows were used is part of the finding, not an implementation
    detail - a user cannot check a comparison they cannot see."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    periods = _investigate(client, dataset_id).json()["periods"]
    assert periods["current"]["start"].startswith("2026-07-01")
    assert periods["previous"]["start"].startswith("2026-06-01")
    # Half-open: the previous window ends exactly where the current one begins.
    assert periods["previous"]["end"] == periods["current"]["start"]
    assert periods["grain"] == "month"


def test_the_primary_driver_is_the_segment_that_actually_moved(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _investigate(client, dataset_id).json()
    assert [d["value"] for d in body["primary_drivers"]] == ["Cairo"]
    driver = body["primary_drivers"][0]
    assert driver["contribution"] == pytest.approx(1.0)
    assert driver["dimension"] == "region"
    # Evidence the UI needs to justify the claim (PRD principle 6).
    assert driver["expected_change"] == pytest.approx(-160.0)
    assert driver["excess_change"] == pytest.approx(-140.0)
    assert driver["previous_rows"] > 0


def test_each_dimension_value_reports_both_period_values(
    client, rca_golden_csv_bytes
) -> None:
    """The core of dimension analysis is both period values per segment, not just
    the delta - a reader cannot check a change they cannot see the sides of.
    Cairo fell 800 -> 500; Giza held at 700."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _investigate(client, dataset_id).json()
    regions = next(d for d in body["dimension_results"] if d["dimension"] == "region")
    by_value = {s["value"]: s for s in regions["segments"]}

    assert by_value["Cairo"]["previous_value"] == pytest.approx(800.0)
    assert by_value["Cairo"]["current_value"] == pytest.approx(500.0)
    assert by_value["Cairo"]["absolute_change"] == pytest.approx(-300.0)
    assert by_value["Cairo"]["percent_change"] == pytest.approx(-37.5)

    assert by_value["Giza"]["previous_value"] == pytest.approx(700.0)
    assert by_value["Giza"]["current_value"] == pytest.approx(700.0)
    assert by_value["Giza"]["absolute_change"] == pytest.approx(0.0)


def test_the_tree_drills_from_the_top_driver_into_the_next_dimension(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    tree = _investigate(client, dataset_id).json()["rca_tree"]

    assert tree["child_dimension"] == "region"
    cairo = next(c for c in tree["children"] if c["value"] == "Cairo")
    product = next(c for c in cairo["children"] if c["value"] == "A")
    assert product["children"][0]["value"] == "Enterprise"
    # Contribution is a share of the global change at every depth, so 100% here
    # means 100% of the KPI movement, not 100% of the parent.
    assert product["children"][0]["contribution"] == pytest.approx(1.0)
    assert [p["value"] for p in product["children"][0]["path"]] == [
        "Cairo",
        "A",
        "Enterprise",
    ]


def test_contributions_sum_to_one_and_the_sum_is_reported(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _investigate(client, dataset_id).json()
    assert body["evidence"]["contribution_sum"] == pytest.approx(1.0)


def test_evidence_reports_the_rows_the_analysis_was_built_from(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    evidence = _investigate(client, dataset_id).json()["evidence"]
    assert evidence["total_rows"] == 8
    assert evidence["current_rows"] == 4
    assert evidence["previous_rows"] == 4
    assert evidence["statements_executed"] > 0


def test_the_summary_describes_contribution_not_causation(
    client, rca_golden_csv_bytes
) -> None:
    """The engine measures which segments moved, not why they moved."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    summary = _investigate(client, dataset_id).json()["summary"].lower()
    assert "contributor" in summary or "contribut" in summary
    assert "caused" not in summary


# --- drivers, offsetting factors and segment lifecycle ------------------------


def test_primary_secondary_and_offsetting_factors_are_separated(
    client, rca_drivers_csv_bytes
) -> None:
    dataset_id = _ready_dataset(
        client,
        rca_drivers_csv_bytes,
        dimensions=["region", "product", "channel"],
    )
    body = _investigate(client, dataset_id).json()

    assert [d["value"] for d in body["primary_drivers"]] == ["Cairo"]
    assert body["primary_drivers"][0]["contribution"] == pytest.approx(0.875)
    assert [d["value"] for d in body["secondary_drivers"]] == ["Luxor"]
    assert [d["value"] for d in body["offsetting_factors"]] == ["Aswan"]
    # An offsetting factor moved the other way, so its contribution is negative.
    assert body["offsetting_factors"][0]["contribution"] == pytest.approx(-0.25)
    assert body["attribution"]["has_offsetting"] is True


def test_new_and_lost_segments_are_flagged_rather_than_shown_as_full_swings(
    client, rca_drivers_csv_bytes
) -> None:
    """"This segment did not exist last month" is a different finding from
    "this segment fell 100%"."""
    dataset_id = _ready_dataset(
        client, rca_drivers_csv_bytes, dimensions=["region", "product", "channel"]
    )
    body = _investigate(client, dataset_id).json()
    regions = {
        node["value"]: node
        for entry in body["dimension_results"]
        if entry["dimension"] == "region"
        for node in entry["segments"]
    }
    assert regions["Aswan"]["is_new_segment"] is True
    assert regions["Luxor"]["is_lost_segment"] is True


def test_the_dimension_that_explains_the_least_ranks_last(
    client, rca_drivers_csv_bytes
) -> None:
    dataset_id = _ready_dataset(
        client, rca_drivers_csv_bytes, dimensions=["region", "product", "channel"]
    )
    body = _investigate(client, dataset_id).json()
    power = {
        s["dimension"]: s["explanatory_power"] for s in body["dimensions_analysed"]
    }
    assert power["region"] > power["product"] > power["channel"]
    assert body["rca_tree"]["child_dimension"] == "region"


# --- the typing trap, end to end ---------------------------------------------


def test_a_currency_formatted_measure_gives_the_same_totals_as_a_numeric_one(
    client, rca_golden_csv_bytes, rca_messy_csv_bytes
) -> None:
    """The same revenue values, one column typed and one arriving as '$500.00'."""
    typed = _investigate(client, _ready_dataset(client, rca_golden_csv_bytes)).json()
    messy = _investigate(client, _ready_dataset(client, rca_messy_csv_bytes)).json()

    assert messy["kpi"]["previous_value"] == pytest.approx(typed["kpi"]["previous_value"])
    assert messy["kpi"]["current_value"] == pytest.approx(typed["kpi"]["current_value"])
    assert messy["kpi"]["absolute_change"] == pytest.approx(typed["kpi"]["absolute_change"])
    assert [d["value"] for d in messy["primary_drivers"]] == [
        d["value"] for d in typed["primary_drivers"]
    ]
    assert messy["evidence"]["unparsed_measure_rows"] == 0


def test_an_excel_dataset_is_analysed_with_the_same_totals_as_a_csv_one(
    client, rca_golden_csv_bytes, tmp_path
) -> None:
    """The strongest guard against the string-typed-Parquet regression.

    ``excel_to_parquet`` writes every column as a string, so an xlsx dataset
    reaches the engine with no real types at all. This runs the genuine
    conversion rather than imitating its output with a CSV.
    """
    from openpyxl import Workbook

    rows = [line.split(",") for line in rca_golden_csv_bytes.decode().strip().splitlines()]
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    path = tmp_path / "golden.xlsx"
    workbook.save(path)

    upload = client.post(
        "/api/uploads",
        files={
            "file": (
                "golden.xlsx",
                path.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    excel_id = upload.json()["dataset"]["id"]
    defined = _define_kpi(client, excel_id)
    assert defined.status_code == 201, defined.text

    excel = _investigate(client, excel_id).json()
    csv = _investigate(client, _ready_dataset(client, rca_golden_csv_bytes)).json()

    assert excel["kpi"]["previous_value"] == pytest.approx(csv["kpi"]["previous_value"])
    assert excel["kpi"]["current_value"] == pytest.approx(csv["kpi"]["current_value"])
    assert excel["kpi"]["absolute_change"] == pytest.approx(-300.0)
    assert [d["value"] for d in excel["primary_drivers"]] == ["Cairo"]
    assert excel["primary_drivers"][0]["contribution"] == pytest.approx(1.0)
    assert excel["evidence"]["unparsed_time_rows"] == 0
    assert excel["evidence"]["unparsed_measure_rows"] == 0


# --- aggregations -------------------------------------------------------------


def test_a_median_kpi_is_reported_as_unattributable_with_no_drivers(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes, aggregation="MEDIAN")
    body = _investigate(client, dataset_id).json()

    assert body["state"] == "unattributable"
    assert body["attribution"]["basis"] == "unattributable"
    assert body["attribution"]["unattributable_reason"] == "distributional_statistic"
    assert body["primary_drivers"] == []
    assert body["rca_tree"] is None
    # The per-segment numbers are still there; only the attribution is withheld.
    segments = [n for e in body["dimension_results"] for n in e["segments"]]
    assert segments
    assert all(node["contribution"] is None for node in segments)
    assert any(n["code"] == "AGGREGATION_NOT_ATTRIBUTABLE" for n in body["notices"])


def test_an_average_kpi_reports_mix_and_rate_effects(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes, aggregation="AVG")
    body = _investigate(client, dataset_id).json()
    assert body["attribution"]["basis"] == "mix_rate"
    segments = [n for e in body["dimension_results"] for n in e["segments"]]
    assert any(node["rate_effect"] is not None for node in segments)
    assert any(node["mix_effect"] is not None for node in segments)


# --- edge cases ---------------------------------------------------------------


def test_a_dataset_with_only_one_period_reports_no_previous_period(client) -> None:
    content = (
        b"order_date,region,product,segment,revenue\n"
        b"2026-07-15,Cairo,A,Enterprise,500\n"
        b"2026-07-16,Giza,B,SMB,400\n"
    )
    dataset_id = _ready_dataset(client, content)
    body = _investigate(client, dataset_id).json()
    assert body["state"] == "no_previous_period"
    assert body["primary_drivers"] == []
    # The page renders this sentence as its description, and absolute_change
    # reads the missing baseline as current - 0, so the generic wording claimed
    # the KPI "increased versus the previous period".
    assert "increased" not in body["summary"]
    assert "no earlier period" in body["summary"]


def test_a_kpi_that_did_not_change_returns_no_drivers_and_says_so(client) -> None:
    content = (
        b"order_date,region,product,segment,revenue\n"
        b"2026-06-15,Cairo,A,Enterprise,500\n"
        b"2026-07-15,Cairo,A,Enterprise,500\n"
    )
    dataset_id = _ready_dataset(client, content)
    body = _investigate(client, dataset_id).json()
    assert body["state"] == "no_change"
    assert body["primary_drivers"] == []
    assert any(n["code"] == "NO_CHANGE_DETECTED" for n in body["notices"])


def test_a_kpi_without_a_time_column_is_rejected(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes, time_column=None)
    response = _investigate(client, dataset_id)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "KPI_TIME_COLUMN_REQUIRED"


def test_a_broad_based_change_names_no_driver(client) -> None:
    """Every region halved, so claiming one of them is the driver would be an
    invention."""
    rows = [b"order_date,region,product,segment,revenue"]
    for region in (b"Cairo", b"Giza", b"Luxor", b"Aswan"):
        rows.append(b"2026-06-15," + region + b",A,Enterprise,400")
        rows.append(b"2026-07-15," + region + b",A,Enterprise,200")
    dataset_id = _ready_dataset(client, b"\n".join(rows) + b"\n")
    body = _investigate(client, dataset_id).json()
    assert body["attribution"]["change_pattern"] == "broad_based"
    assert body["primary_drivers"] == []
    assert body["rca_tree"] is None


def test_a_zero_previous_value_returns_a_null_percent_change(client) -> None:
    content = (
        b"order_date,region,product,segment,revenue\n"
        b"2026-06-15,Cairo,A,Enterprise,0\n"
        b"2026-07-15,Cairo,A,Enterprise,500\n"
    )
    dataset_id = _ready_dataset(client, content)
    kpi = _investigate(client, dataset_id).json()["kpi"]
    assert kpi["percent_change"] is None
    assert kpi["percent_change_undefined_reason"] == "zero_baseline"


def test_the_pareto_target_used_for_ranking_is_reported(client, rca_golden_csv_bytes) -> None:
    """The one conventional constant in the ranking, so it is disclosed rather
    than hidden in the engine."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    attribution = _investigate(client, dataset_id).json()["attribution"]
    assert attribution["pareto_target"] == pytest.approx(0.80)
    assert attribution["min_material_contribution"] == pytest.approx(0.05)


def test_the_tree_depth_can_be_limited_by_the_request(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    tree = _investigate(client, dataset_id, max_tree_depth=1).json()["rca_tree"]
    cairo = next(c for c in tree["children"] if c["value"] == "Cairo")
    assert cairo["children"] == []
    assert cairo["stop_reason"] == "max_depth_reached"


def _four_equal_drivers() -> bytes:
    """Four regions each moving a quarter of the change, beside one that held.

    Equal quarters make Pareto run to all four, so a cap is observable; the
    region that held keeps the change from being proportional, without which no
    dimension clears MIN_EXPLANATORY_POWER and nothing is named at all.
    """
    rows = ["order_date,region,product,segment,revenue"]
    for name, current in (("r1", 700), ("r2", 700), ("r3", 700), ("r4", 700)):
        rows.append(f"2026-06-15,{name},A,Enterprise,1000")
        rows.append(f"2026-07-15,{name},A,Enterprise,{current}")
    rows.append("2026-06-15,r5,A,Enterprise,4000")
    rows.append("2026-07-15,r5,A,Enterprise,4000")
    return ("\n".join(rows) + "\n").encode()


def test_the_number_of_named_drivers_can_be_limited_by_the_request(client) -> None:
    """max_tree_depth had a test; its sibling knob is the control §13 asks for
    over how many candidates come back."""
    dataset_id = _ready_dataset(client, _four_equal_drivers())
    uncapped = _investigate(client, dataset_id).json()
    assert len(uncapped["primary_drivers"]) == 4

    capped = _investigate(client, dataset_id, max_drivers=1).json()
    assert len(capped["primary_drivers"]) == 1


def test_a_high_cardinality_dimension_is_truncated_without_losing_the_total(
    client,
) -> None:
    """Everything past the top-K goes into one residual bucket, so the
    contributions still add to 100% and the response says it truncated."""
    rows = ["order_date,region,product,segment,revenue"]
    for i in range(80):
        rows.append(f"2026-06-15,r{i:02d},A,Enterprise,1000")
        rows.append(f"2026-07-15,r{i:02d},A,Enterprise,{200 if i == 0 else 1000}")
    dataset_id = _ready_dataset(client, ("\n".join(rows) + "\n").encode())
    body = _investigate(client, dataset_id).json()

    assert any(n["code"] == "DIMENSION_TRUNCATED" for n in body["notices"])
    summary = next(d for d in body["dimensions_analysed"] if d["dimension"] == "region")
    assert summary["truncated"] is True
    regions = next(d for d in body["dimension_results"] if d["dimension"] == "region")
    assert any(s["is_other_bucket"] for s in regions["segments"])
    assert body["evidence"]["contribution_sum"] == pytest.approx(1.0)


def test_every_truncated_dimension_gets_its_own_notice(client) -> None:
    """One notice per dimension, so a reader learns *which* ones truncated.

    That makes the notice code non-unique within the list, which is the contract
    the UI keys on - keying by code alone silently drops list entries.
    """
    rows = ["order_date,region,product,segment,revenue"]
    for i in range(80):
        for day, amount in (("06-15", 1000), ("07-15", 200 if i == 0 else 1000)):
            rows.append(f"2026-{day},r{i:02d},p{i:02d},Enterprise,{amount}")
    dataset_id = _ready_dataset(client, ("\n".join(rows) + "\n").encode())
    body = _investigate(client, dataset_id).json()

    truncated = [n for n in body["notices"] if n["code"] == "DIMENSION_TRUNCATED"]
    assert {n["details"]["dimension"] for n in truncated} == {"region", "product"}


def test_a_null_dimension_value_arrives_as_its_own_segment(client) -> None:
    """Both halves have to survive serialisation: ``value`` null *and*
    ``value_is_null`` true. The table renders "(no value)" off the flag, so a
    null that arrived as the string "None" would pass every engine test."""
    content = (
        b"order_date,region,product,segment,revenue\n"
        b"2026-06-15,Cairo,A,Enterprise,500\n"
        b"2026-06-15,Giza,A,Enterprise,400\n"
        b"2026-06-15,Luxor,A,Enterprise,300\n"
        b"2026-06-15,,A,Enterprise,300\n"
        b"2026-07-15,Cairo,A,Enterprise,200\n"
        b"2026-07-15,Giza,A,Enterprise,400\n"
        b"2026-07-15,Luxor,A,Enterprise,300\n"
        b"2026-07-15,,A,Enterprise,100\n"
    )
    dataset_id = _ready_dataset(client, content)
    body = _investigate(client, dataset_id).json()
    regions = next(d for d in body["dimension_results"] if d["dimension"] == "region")
    blank = next(s for s in regions["segments"] if s["value_is_null"])
    assert blank["value"] is None
    assert blank["previous_value"] == pytest.approx(300.0)
    assert blank["current_value"] == pytest.approx(100.0)


def test_a_kpi_with_no_dimensions_reports_the_change_without_drivers(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes, dimensions=[])
    body = _investigate(client, dataset_id).json()
    assert body["state"] == "ok"
    assert body["kpi"]["absolute_change"] == pytest.approx(-300.0)
    assert body["primary_drivers"] == []
    assert body["rca_tree"] is None
    assert any(n["code"] == "NO_DIMENSIONS_CONFIGURED" for n in body["notices"])
    # Not "no segment accounts for a material share": there was nothing to split.
    assert "no analysis dimensions" in body["summary"]


# --- preconditions and tenancy -----------------------------------------------


def test_an_investigation_requires_an_analysis_ready_dataset(
    client, rca_golden_csv_bytes
) -> None:
    upload = _upload(client, rca_golden_csv_bytes)
    dataset_id = upload.json()["dataset"]["id"]  # profiled, but no KPI yet
    response = _investigate(client, dataset_id)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_ANALYSIS_READY"


def test_a_missing_dataset_returns_not_found(client) -> None:
    response = _investigate(client, "00000000-0000-0000-0000-0000000000ff")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_an_explicit_kpi_definition_id_is_honoured(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    active = client.get(f"/api/datasets/{dataset_id}/kpi-definitions/active").json()
    response = _investigate(client, dataset_id, kpi_definition_id=active["id"])
    assert response.status_code == 200, response.text
    assert response.json()["kpi_definition_id"] == active["id"]


def test_a_kpi_definition_from_another_dataset_is_rejected(
    client, rca_golden_csv_bytes
) -> None:
    first = _ready_dataset(client, rca_golden_csv_bytes)
    second = _ready_dataset(client, rca_golden_csv_bytes)
    other = client.get(f"/api/datasets/{second}/kpi-definitions/active").json()

    response = _investigate(client, first, kpi_definition_id=other["id"])
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "KPI_DEFINITION_NOT_FOUND"


def test_another_company_cannot_investigate_this_dataset(
    client, rca_golden_csv_bytes, other_company
) -> None:
    """Cross-tenant access is a 404, not a 403 - existence is not disclosed."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    response = client.post(
        "/api/rca/investigations",
        json={"dataset_id": dataset_id},
        headers={"X-Company-Id": str(other_company)},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


# --- DELETE /api/rca/investigations/{dataset_id} ------------------------------


def test_deleting_an_investigation_takes_the_dataset_off_the_list(
    client, rca_golden_csv_bytes
) -> None:
    """The dataset survives; only the KPI that made it investigable goes."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    assert _investigate(client, dataset_id).status_code == 200

    response = client.delete(f"/api/rca/investigations/{dataset_id}")
    assert response.status_code == 204, response.text

    dataset = client.get(f"/api/datasets/{dataset_id}")
    assert dataset.status_code == 200, dataset.text
    assert dataset.json()["status"] == "profiled"

    # No longer investigable, and no active definition to compute from.
    assert _investigate(client, dataset_id).status_code == 409
    assert client.get(f"/api/datasets/{dataset_id}/kpi-definitions/active").status_code == 404


def test_deleting_an_investigation_twice_reports_the_second_attempt(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    assert client.delete(f"/api/rca/investigations/{dataset_id}").status_code == 204

    response = client.delete(f"/api/rca/investigations/{dataset_id}")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "KPI_DEFINITION_NOT_FOUND"


def test_deleting_an_investigation_for_a_missing_dataset_is_not_found(client) -> None:
    response = client.delete("/api/rca/investigations/00000000-0000-0000-0000-0000000000ff")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_another_company_cannot_delete_this_investigation(
    client, rca_golden_csv_bytes, other_company
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    response = client.delete(
        f"/api/rca/investigations/{dataset_id}",
        headers={"X-Company-Id": str(other_company)},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"

    # The real owner's investigation is untouched.
    assert _investigate(client, dataset_id).status_code == 200


def test_the_demo_engine_is_gone_and_investigations_is_a_real_resource(client) -> None:
    """The pre-Phase-1 engine divided by gross movement, which reported segments
    moving *against* the KPI as top drivers. It must not linger.

    ``POST /api/investigations`` used to 404 for the same reason. It is now the
    persisted, evidence-backed endpoint, so the assertion is that it exists and
    validates its body - not that it is absent.
    """
    assert client.get("/api/demo").status_code == 404
    assert client.post("/api/investigations", json={}).status_code == 422
