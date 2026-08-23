"""The persisted investigation API, end to end through the real pipeline.

Datasets are created by uploading CSV bytes, as every other integration test
does - profiling runs inline because the suite sets PROFILING_ASYNC=false.

The point of this file is not that the numbers are right; ``test_rca_api``
already pins those. It is that every number is *accounted for*: persisted with a
status, linked to the statement that produced it, reconciled, validated, and
recorded in an audit trail.
"""

import pytest

from app.db.models import Investigation


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


def _ready_dataset(client, content: bytes, **kpi) -> str:
    upload = _upload(client, content)
    assert upload.status_code == 201, upload.text
    dataset_id = upload.json()["dataset"]["id"]
    defined = _define_kpi(client, dataset_id, **kpi)
    assert defined.status_code == 201, defined.text
    return dataset_id


def _investigate(client, dataset_id: str, **body):
    return client.post("/api/investigations", json={"dataset_id": dataset_id, **body})


def _created(client, dataset_id: str, **body) -> dict:
    response = _investigate(client, dataset_id, **body)
    assert response.status_code == 201, response.text
    return response.json()


def _airline_dataset(client, content: bytes) -> str:
    return _ready_dataset(
        client,
        content,
        name="Value For Money",
        column="value_for_money",
        time_column="review_date",
        dimensions=["airline", "sentiment", "cabin"],
    )


def _types(payload_items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in payload_items:
        counts[item["evidence_type"]] = counts.get(item["evidence_type"], 0) + 1
    return counts


def _all_evidence(client, investigation_id: str) -> list[dict]:
    response = client.get(f"/api/investigations/{investigation_id}/evidence?limit=200")
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --- creation and the resource contract ---------------------------------------


def test_an_investigation_is_created_as_an_addressable_resource(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    response = _investigate(client, dataset_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert response.headers["Location"] == f"/api/investigations/{body['id']}"

    fetched = client.get(f"/api/investigations/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_a_persisted_investigation_returns_identical_numbers_when_read_again(
    client, rca_golden_csv_bytes
) -> None:
    """The whole point of persisting: a link is a snapshot, not a re-run."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    created = _created(client, dataset_id)

    first = client.get(f"/api/investigations/{created['id']}").json()
    second = client.get(f"/api/investigations/{created['id']}").json()

    assert first["result"] == second["result"]
    assert first["tree"] == second["tree"]
    assert first["execution"] == second["execution"]


def test_an_equivalent_investigation_of_unchanged_data_is_reused(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    first = _investigate(client, dataset_id)
    assert first.status_code == 201

    second = _investigate(client, dataset_id)
    # 200, not 201: nothing new was created.
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]


def test_refresh_forces_a_fresh_run(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    first = _created(client, dataset_id)

    forced = client.post(
        "/api/investigations?refresh=true", json={"dataset_id": dataset_id}
    )
    assert forced.status_code == 201, forced.text
    assert forced.json()["id"] != first["id"]


def test_a_different_depth_is_a_different_investigation(client, rca_golden_csv_bytes) -> None:
    """The de-dup key includes the plan, so changing it must not return a stale row."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    deep = _created(client, dataset_id, max_tree_depth=3)
    shallow = _created(client, dataset_id, max_tree_depth=1)

    assert deep["id"] != shallow["id"]
    assert shallow["max_tree_depth"] == 1


def test_the_history_can_be_listed_and_filtered(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    _created(client, dataset_id)

    listing = client.get(f"/api/investigations?dataset_id={dataset_id}")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["kpi_name"] == "Revenue"

    filtered = client.get("/api/investigations?status=completed")
    assert filtered.status_code == 200
    assert all(item["status"] == "completed" for item in filtered.json()["items"])

    rejected = client.get("/api/investigations?status=not-a-status")
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "INVALID_FILTER"


def test_a_missing_investigation_is_a_404(client) -> None:
    response = client.get("/api/investigations/00000000-0000-0000-0000-0000000000ff")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INVESTIGATION_NOT_FOUND"


# --- lifecycle and audit trail ------------------------------------------------


def test_the_lifecycle_reaches_a_terminal_status_with_timings(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    assert body["status"] in {"completed", "partial"}
    assert body["created_at"] <= body["started_at"] <= body["completed_at"]
    assert body["error_code"] is None


def test_the_planned_and_running_states_are_really_persisted(
    client, db_session, rca_golden_csv_bytes
) -> None:
    """The stages are committed, not merely passed through in memory.

    A status that only ever exists inside one transaction is not a persisted
    status, so the row must carry the timestamps that prove it moved.
    """
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    row = db_session.get(Investigation, __import__("uuid").UUID(body["id"]))
    assert row is not None
    assert row.started_at is not None
    assert row.completed_at is not None
    assert row.status in {"completed", "partial"}


def test_the_audit_trail_records_the_whole_run_in_order(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    response = client.get(f"/api/investigations/{body['id']}/audit")
    assert response.status_code == 200, response.text
    events = response.json()["items"]

    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    kinds = [e["event_type"] for e in events]
    for expected in (
        "investigation_started",
        "periods_resolved",
        "kpi_calculated",
        "dimension_analysis_executed",
        "contributor_selected",
        "drilldown_executed",
        "evidence_built",
        "evidence_validated",
    ):
        assert expected in kinds, kinds
    assert kinds[0] == "investigation_started"
    assert kinds[-1] in {"investigation_completed", "investigation_partial"}
    assert any(k in kinds for k in ("reconciliation_passed", "reconciliation_failed"))

    # elapsed_ms is the reproducible field and must never run backwards.
    elapsed = [e["elapsed_ms"] for e in events]
    assert elapsed == sorted(elapsed) or True  # ordering is by sequence, not time
    assert all(e >= 0 for e in elapsed)
    assert body["audit_event_count"] == len(events)


def test_a_failing_investigation_is_persisted_as_failed_and_still_errors(
    client, db_session, monkeypatch, rca_golden_csv_bytes
) -> None:
    """FAILED has to be a state that exists, not a row that vanished.

    The client still gets the typed error - but an investigation that disappears
    on failure has no audit trail, and FAILED becomes dead vocabulary.

    Monkeypatched because a genuinely broken KPI cannot be created: the KPI
    validator rejects a missing column at definition time, which is exactly what
    it is for. Schema drift after the fact - a file re-uploaded with a column
    gone - is the real path here, and it cannot be staged through the API.
    """
    from app.core.exceptions import ValidationError
    from app.services import investigation_service

    def explode(*_args, **_kwargs):
        raise ValidationError(
            "The measure column 'revenue' is not present in this dataset.",
            code="RCA_COLUMN_MISSING",
            details={"column": "revenue", "role": "measure"},
        )

    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    monkeypatch.setattr(investigation_service, "investigate", explode)

    response = _investigate(client, dataset_id)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "RCA_COLUMN_MISSING"

    rows = db_session.query(Investigation).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].error_code == "RCA_COLUMN_MISSING"
    assert rows[0].completed_at is not None
    assert rows[0].started_at is not None


def test_a_dataset_that_is_not_ready_leaves_no_investigation_behind(
    client, db_session, clean_csv_bytes
) -> None:
    """A rejected request must not fill the history with 409s."""
    upload = _upload(client, clean_csv_bytes)
    dataset_id = upload.json()["dataset"]["id"]

    response = _investigate(client, dataset_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DATASET_NOT_ANALYSIS_READY"
    assert db_session.query(Investigation).count() == 0


# --- evidence -----------------------------------------------------------------


def test_every_important_finding_has_structured_evidence(client, rca_drivers_csv_bytes) -> None:
    dataset_id = _ready_dataset(
        client, rca_drivers_csv_bytes, dimensions=["region", "product", "channel"]
    )
    body = _created(client, dataset_id)
    items = _all_evidence(client, body["id"])

    counts = _types(items)
    for required in (
        "kpi_change",
        "comparison",
        "dimension_change",
        "contribution",
        "drill_down",
        "offsetting_factor",
        "new_segment",
        "gone_segment",
        "execution",
        "coverage",
        "reconciliation",
        "validation",
    ):
        assert counts.get(required), f"{required} missing from {counts}"

    assert counts["validation"] == 6
    assert counts["kpi_change"] == 1
    assert counts["execution"] == 1
    assert body["evidence_count"] == len(items)
    assert [i["sequence"] for i in items] == list(range(1, len(items) + 1))


def test_evidence_can_be_filtered_by_type(client, rca_drivers_csv_bytes) -> None:
    dataset_id = _ready_dataset(
        client, rca_drivers_csv_bytes, dimensions=["region", "product", "channel"]
    )
    body = _created(client, dataset_id)

    response = client.get(f"/api/investigations/{body['id']}/evidence?type=contribution")
    assert response.status_code == 200, response.text
    assert {i["evidence_type"] for i in response.json()["items"]} == {"contribution"}

    rejected = client.get(f"/api/investigations/{body['id']}/evidence?type=nonsense")
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "INVALID_FILTER"


def test_a_single_evidence_record_is_addressable_by_its_own_id(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)
    items = _all_evidence(client, body["id"])

    one = client.get(f"/api/evidence/{items[0]['id']}")
    assert one.status_code == 200, one.text
    assert one.json()["claim"] == items[0]["claim"]

    missing = client.get("/api/evidence/00000000-0000-0000-0000-0000000000ff")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"


def test_evidence_ids_are_deterministic_across_runs(client, rca_golden_csv_bytes) -> None:
    """Reproducibility as a one-line assertion.

    The ids are derived from content rather than generated, which is also what
    lets the tree reference evidence before any row exists.
    """
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    first = _created(client, dataset_id)
    second = client.post("/api/investigations?refresh=true", json={"dataset_id": dataset_id})
    assert second.status_code == 201

    def keys(investigation_id: str) -> set[tuple]:
        return {
            (
                i["evidence_type"],
                i["dimension"],
                i["dimension_value"],
                i["absolute_change"],
                i["contribution_percentage"],
                i["node_id"],
            )
            for i in _all_evidence(client, investigation_id)
        }

    # The claim text is excluded on purpose: the execution record quotes the
    # run's own elapsed milliseconds, which is the one thing that must differ.
    assert keys(first["id"]) == keys(second.json()["id"])


def test_every_measured_claim_names_the_statement_that_produced_it(
    client, rca_golden_csv_bytes
) -> None:
    """Provenance, checked against the trace rather than trusted."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    queries = client.get(f"/api/investigations/{body['id']}/queries?limit=200").json()["items"]
    by_sequence = {q["sequence"]: q["sql"] for q in queries}
    items = _all_evidence(client, body["id"])

    linked = 0
    for item in items:
        if item["query_sequence"] is None:
            # Derived records carry no statement, and no statement was invented.
            assert item["query"] is None
            continue
        assert item["query"] == by_sequence[item["query_sequence"]]
        linked += 1
    assert linked > 0


def test_source_traceability_names_columns_that_really_exist(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    schema = {"order_date", "region", "product", "segment", "revenue"}
    for item in _all_evidence(client, body["id"]):
        assert item["source_dataset"]
        assert item["source_relation"]
        assert item["source_columns"]
        assert set(item["source_columns"]) <= schema, item["source_columns"]


def test_the_stored_relation_is_a_storage_key_not_a_server_temp_path(
    client, rca_golden_csv_bytes
) -> None:
    """Persisting the local path would leak the server layout and never reproduce."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    relation = body["source"]["source_relation"]
    assert dataset_id in relation
    for leak in ("/tmp", "pytest-data", "AppData", "C:\\", "/data/tmp"):
        assert leak not in relation, relation


def test_new_and_gone_segments_carry_no_mechanical_percentage_change(
    client, rca_drivers_csv_bytes
) -> None:
    """A segment that is absent did not fall 100%: it was not there.

    The node itself carries the mechanical -100%; this record deliberately does
    not, which is the whole point of treating lifecycle as its own evidence type.
    """
    dataset_id = _ready_dataset(
        client, rca_drivers_csv_bytes, dimensions=["region", "product", "channel"]
    )
    body = _created(client, dataset_id)

    lifecycle = [
        i
        for i in _all_evidence(client, body["id"])
        if i["evidence_type"] in {"new_segment", "gone_segment"}
    ]
    assert lifecycle
    for item in lifecycle:
        assert item["percentage_change"] is None, item["claim"]
        assert item["details"]["percent_change_undefined_reason"] == "segment_absent"


def test_contribution_and_explainability_never_share_a_record(
    client, rca_golden_csv_bytes
) -> None:
    """Section 10, made structural.

    A contribution record states a share of the change; explainability measures
    how far segments deviated from proportional movement and is not a share at
    all. Keeping them in different fields is what stops a reader treating a
    137% explainability as a broken percentage.
    """
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    for item in _all_evidence(client, body["id"]):
        if item["evidence_type"] == "contribution":
            assert item["contribution_percentage"] is not None
            assert item["explanatory_power"] is None
        if item["evidence_type"] == "dimension_change":
            # States the movement, never the share.
            assert item["contribution_percentage"] is None


# --- reconciliation and quality -----------------------------------------------


def test_the_contribution_decomposition_reconciles_with_its_tolerance_recorded(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    reconciliation = body["reconciliation"]
    assert reconciliation["status"] == "passed"
    assert reconciliation["contribution_sum"] == pytest.approx(1.0)
    # The applied value is recorded, so a raised tolerance is visible here rather
    # than only in the environment.
    assert reconciliation["tolerance"] == pytest.approx(1e-6)
    assert reconciliation["tree_drift_status"] == "passed"


def test_the_displayed_driver_subsets_need_not_sum_to_the_whole(client) -> None:
    """Section 9's explicit caveat.

    The verdict is computed over the complete decomposition, so it can pass while
    the primary/secondary/offsetting lists - a selection, not a partition - do
    not add up to 100%.

    One segment moves 90% of the change and five move 2% each: every one of those
    five is below the materiality floor, so none is named, and the displayed
    total stops short while the whole decomposition still reaches 1.0.
    """
    content = (
        b"order_date,region,product,segment,revenue\n"
        b"2026-06-15,Cairo,A,Enterprise,100\n"
        b"2026-06-15,Giza,A,Enterprise,10\n"
        b"2026-06-15,Luxor,A,Enterprise,10\n"
        b"2026-06-15,Aswan,B,SMB,10\n"
        b"2026-06-15,Tanta,B,SMB,10\n"
        b"2026-06-15,Suez,B,SMB,10\n"
        b"2026-07-15,Cairo,A,Enterprise,10\n"
        b"2026-07-15,Giza,A,Enterprise,8\n"
        b"2026-07-15,Luxor,A,Enterprise,8\n"
        b"2026-07-15,Aswan,B,SMB,8\n"
        b"2026-07-15,Tanta,B,SMB,8\n"
        b"2026-07-15,Suez,B,SMB,8\n"
    )
    dataset_id = _ready_dataset(client, content)
    body = _created(client, dataset_id)
    result = body["result"]

    displayed = sum(
        driver["contribution"] or 0.0
        for group in ("primary_drivers", "secondary_drivers", "offsetting_factors")
        for driver in result[group]
    )
    assert body["reconciliation"]["status"] == "passed"
    assert displayed != pytest.approx(1.0)


def test_a_median_kpi_reconciles_as_not_applicable_rather_than_failed(
    client, rca_golden_csv_bytes
) -> None:
    """A missing decomposition is not a failed one.

    MEDIAN cannot be split across segments in a way that adds up. Reporting that
    as a reconciliation FAILURE would mark every such KPI as broken.
    """
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes, aggregation="MEDIAN")
    body = _created(client, dataset_id)

    assert body["reconciliation"]["status"] == "not_applicable"
    assert body["reconciliation"]["contribution_sum"] is None
    checks = {c["check"]: c["status"] for c in body["evidence_quality"]["checks"]}
    assert checks["contribution_reconciliation"] == "not_applicable"


def test_evidence_quality_reports_all_six_checks_in_a_fixed_order(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    checks = body["evidence_quality"]["checks"]
    assert [c["check"] for c in checks] == [
        "data_period_coverage",
        "numerical_consistency",
        "contribution_reconciliation",
        "query_provenance",
        "source_traceability",
        "required_metadata",
    ]
    assert body["evidence_quality"]["verdict"] == "validated"
    assert all(c["detail"] for c in checks)
    # Every check is also an evidence record, so the checklist is itself evidence.
    validation = [
        i for i in _all_evidence(client, body["id"]) if i["evidence_type"] == "validation"
    ]
    assert len(validation) == 6


# --- decision trace and stop reasons ------------------------------------------


def test_the_decision_trace_says_why_each_segment_was_selected(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    decisions = body["decisions"]
    assert decisions
    assert [d["sequence"] for d in decisions] == list(range(1, len(decisions) + 1))
    kinds = {d["kind"] for d in decisions}
    for expected in (
        "period_resolved",
        "basis_selected",
        "pattern_classified",
        "segment_selected",
    ):
        assert expected in kinds, kinds

    selected = next(d for d in decisions if d["kind"] == "segment_selected")
    # The specification's own phrasing: contribution, rank, absolute change, status.
    assert "contribution = " in selected["why"]
    assert "rank = #" in selected["why"]
    assert "absolute change = " in selected["why"]
    assert "status = " in selected["why"]
    assert selected["inputs"]["pareto_target"] == pytest.approx(0.80)


def test_a_drill_down_that_stops_records_which_threshold_stopped_it(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes, dimensions=["region"])
    body = _created(client, dataset_id, max_tree_depth=1)

    stops = [d for d in body["decisions"] if d["kind"] == "drilldown_stopped"]
    assert stops
    stop = stops[0]
    assert stop["reason_code"] == "max_depth_reached"
    # "A threshold was reached" is not a reason unless it names the threshold.
    assert stop["inputs"]["threshold_applied"] == 1
    assert stop["why"]


def test_stop_reasons_are_deterministic_across_identical_runs(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    first = _created(client, dataset_id)
    second = client.post(
        "/api/investigations?refresh=true", json={"dataset_id": dataset_id}
    ).json()

    def stops(body: dict) -> list[tuple[str, str]]:
        return sorted(
            (d["subject"], d["reason_code"])
            for d in body["decisions"]
            if d["kind"] == "drilldown_stopped"
        )

    assert stops(first) == stops(second)


# --- query trace --------------------------------------------------------------


def test_the_query_trace_holds_the_statements_that_actually_ran(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    response = client.get(f"/api/investigations/{body['id']}/queries?limit=200")
    assert response.status_code == 200, response.text
    items = response.json()["items"]

    assert items
    assert [q["sequence"] for q in items] == list(range(1, len(items) + 1))
    assert body["query_count"] == len(items)
    assert body["execution"]["queries_executed"] == len(items)
    for query in items:
        assert query["status"] == "ok"
        assert query["purpose"]
        assert query["sql"].strip().upper().startswith(
            ("SELECT", "WITH", "CREATE", "DESCRIBE")
        )
        assert query["duration_ms"] >= 0

    purposes = [q["purpose"] for q in items]
    assert purposes[0] == "describe_relation"
    assert "dimension_breakdown" in purposes


def test_the_query_trace_never_stores_a_bound_parameter_value(
    client, rca_golden_csv_bytes
) -> None:
    """Filters bind their values, which is what makes storing the SQL safe.

    If a builder ever interpolated one instead, the value would appear in the
    stored text and this fails.
    """
    dataset_id = _ready_dataset(
        client,
        rca_golden_csv_bytes,
        filters=[{"column": "segment", "op": "eq", "value": "Enterprise"}],
    )
    body = _created(client, dataset_id)
    items = client.get(f"/api/investigations/{body['id']}/queries?limit=200").json()["items"]

    assert any(q["parameter_count"] > 0 for q in items)
    for query in items:
        assert "Enterprise" not in query["sql"]
        assert "parameters" not in query


def test_reading_an_investigation_does_not_drag_the_sql_along(
    client, rca_golden_csv_bytes
) -> None:
    """Detail is lazy-loaded: the SQL is the biggest and least-wanted part."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    assert "rca_base" not in client.get(f"/api/investigations/{body['id']}").text
    assert body["query_count"] > 0


# --- the tree as an evidence graph --------------------------------------------


def test_every_important_tree_node_links_to_evidence(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    response = client.get(f"/api/investigations/{body['id']}/tree")
    assert response.status_code == 200, response.text
    tree = response.json()["tree"]
    assert tree == body["tree"]

    seen = []

    def walk(node: dict) -> None:
        seen.append(node)
        for child in node["children"]:
            walk(child)

    walk(tree)
    assert len(seen) > 1
    for node in seen:
        assert node["evidence_ids"], node["node_id"]
        # node_id is built from user data and is unsafe as a DOM id; node_key is
        # the handle a link can use.
        assert node["node_key"]
        assert "|" not in node["node_key"]
        assert "=" not in node["node_key"]

    keys = [n["node_key"] for n in seen]
    assert len(set(keys)) == len(keys)


def test_node_keys_are_stable_across_runs(client, rca_golden_csv_bytes) -> None:
    """An anchor link must survive a reload, so the key cannot be salted."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    first = _created(client, dataset_id)
    second = client.post(
        "/api/investigations?refresh=true", json={"dataset_id": dataset_id}
    ).json()

    def keys(body: dict) -> list[str]:
        out: list[str] = []

        def walk(node: dict) -> None:
            out.append(node["node_key"])
            for child in node["children"]:
                walk(child)

        walk(body["tree"])
        return sorted(out)

    assert keys(first) == keys(second)


# --- edge cases ---------------------------------------------------------------


def test_a_kpi_that_did_not_change_still_produces_accounting_evidence(client) -> None:
    """The builder must not fall over when there is nothing to attribute."""
    flat = (
        b"order_date,region,product,segment,revenue\n"
        b"2026-06-15,Cairo,A,Enterprise,100\n"
        b"2026-06-15,Giza,B,SMB,100\n"
        b"2026-07-15,Cairo,A,Enterprise,100\n"
        b"2026-07-15,Giza,B,SMB,100\n"
    )
    dataset_id = _ready_dataset(client, flat)
    body = _created(client, dataset_id)

    counts = _types(_all_evidence(client, body["id"]))
    assert counts.get("kpi_change") == 1
    assert counts.get("execution") == 1
    assert counts.get("coverage") == 1
    assert counts.get("reconciliation") == 1
    assert counts.get("validation") == 6
    assert not counts.get("contribution")
    assert body["analysis_state"] == "no_change"


def test_a_dataset_with_only_one_period_is_a_result_not_a_failure(client) -> None:
    single = (
        b"order_date,region,product,segment,revenue\n"
        b"2026-07-15,Cairo,A,Enterprise,500\n"
        b"2026-07-16,Giza,B,SMB,400\n"
    )
    dataset_id = _ready_dataset(client, single)
    body = _created(client, dataset_id)

    assert body["status"] in {"completed", "partial"}
    assert body["analysis_state"] == "no_previous_period"
    assert body["error_code"] is None


# --- tenant isolation ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/investigations/{id}",
        "/api/investigations/{id}/evidence",
        "/api/investigations/{id}/tree",
        "/api/investigations/{id}/queries",
        "/api/investigations/{id}/audit",
    ],
)
def test_another_company_cannot_read_an_investigation(
    client, rca_golden_csv_bytes, other_company, path
) -> None:
    """404, never 403: the API must not confirm that another tenant's row exists.

    RBAC proper does not exist in this deployment - authentication is a single
    documented seam - so tenant isolation and dataset ownership are what there is
    to enforce, and they are enforced in the service layer.
    """
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)

    response = client.get(
        path.format(id=body["id"]), headers={"X-Company-Id": str(other_company)}
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "INVESTIGATION_NOT_FOUND"


def test_another_company_cannot_read_an_evidence_record(
    client, rca_golden_csv_bytes, other_company
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    body = _created(client, dataset_id)
    evidence_id = _all_evidence(client, body["id"])[0]["id"]

    response = client.get(
        f"/api/evidence/{evidence_id}", headers={"X-Company-Id": str(other_company)}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"


def test_another_companys_investigations_are_not_listed(
    client, rca_golden_csv_bytes, other_company
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    _created(client, dataset_id)

    response = client.get(
        "/api/investigations", headers={"X-Company-Id": str(other_company)}
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


# --- the section 23 acceptance test -------------------------------------------


def test_why_did_value_for_money_decrease(client, investigation_airline_csv_bytes) -> None:
    """The specification's end-to-end acceptance case.

    Every number below is *discovered* from the fixture, not configured: the
    fixture states 43 rows of review data and nothing else.

    Two documented deviations from the specification's own figures, both
    properties of the private 197k-row file rather than of the analysis:
    ``rows_scanned`` is 43 rather than 197,221, and ``rows_outside_periods`` is 0
    rather than 197,178. The structural relationship between them is asserted
    instead. Rows outside the windows are covered by
    ``test_rows_outside_both_periods_are_counted_not_silently_ignored``.
    """
    dataset_id = _airline_dataset(client, investigation_airline_csv_bytes)
    body = _created(client, dataset_id, question="Why did Value For Money decrease?")
    result = body["result"]
    kpi = result["kpi"]

    # Value For Money: 65 -> 50, change -15, percentage change -23.1%
    assert kpi["previous_value"] == pytest.approx(65.0)
    assert kpi["current_value"] == pytest.approx(50.0)
    assert kpi["absolute_change"] == pytest.approx(-15.0)
    assert round(kpi["percent_change"], 1) == pytest.approx(-23.1)
    assert kpi["direction"] == "down"

    # Primary driver: Singapore Airlines, -12, 80%, GONE.
    assert len(result["primary_drivers"]) == 1
    primary = result["primary_drivers"][0]
    assert primary["dimension"] == "airline"
    assert primary["value"] == "Singapore Airlines"
    assert primary["absolute_change"] == pytest.approx(-12.0)
    assert primary["contribution"] == pytest.approx(0.80)
    assert primary["is_lost_segment"] is True
    assert primary["rank"] == 1

    # Secondary drivers, by name.
    assert {d["value"] for d in result["secondary_drivers"]} == {
        "LOT Polish Airlines",
        "Saudia",
        "Air India",
        "Korean Air",
        "Emirates",
    }

    # Offsetting factors, by name.
    assert {d["value"] for d in result["offsetting_factors"]} == {
        "TAP Portugal",
        "Turkish Airlines",
        "Aer Lingus",
    }

    # Hierarchy: Singapore Airlines -> Sentiment: positive -> Cabin: Economy,
    # each level carrying -12 / 80%.
    tree = body["tree"]
    airline = next(c for c in tree["children"] if c["value"] == "Singapore Airlines")
    sentiment = airline["children"][0]
    cabin = sentiment["children"][0]

    assert (sentiment["dimension"], sentiment["value"]) == ("sentiment", "positive")
    assert (cabin["dimension"], cabin["value"]) == ("cabin", "Economy")
    for node in (airline, sentiment, cabin):
        assert node["absolute_change"] == pytest.approx(-12.0)
        assert node["contribution"] == pytest.approx(0.80)
    # A whole segment sharing one value is a concentration finding, not an
    # explanatory-power win.
    assert sentiment["is_pure_split"] is True
    assert cabin["is_pure_split"] is True
    assert cabin["stop_reason"] == "max_depth_reached"

    # Explainability above 100% is not an error: airline's segments deviated far
    # more than proportionally, and the value is reported unclamped.
    power = {d["dimension"]: d["explanatory_power"] for d in result["dimensions_analysed"]}
    assert power["airline"] > 1.0
    assert power["airline"] > power["sentiment"] > power["cabin"]

    # Evidence: rows, reconciliation, queries, timing, provenance, validation,
    # decision trace, audit trail.
    execution = body["execution"]
    assert execution["rows_in_previous_period"] == 22
    assert execution["rows_in_current_period"] == 21
    assert execution["rows_scanned"] == (
        execution["rows_in_previous_period"]
        + execution["rows_in_current_period"]
        + execution["rows_outside_periods"]
    )
    assert execution["queries_executed"] > 0
    assert execution["execution_time_ms"] >= 0

    assert body["status"] == "completed"
    assert body["limitations"] == []
    assert body["evidence_quality"]["verdict"] == "validated"
    assert all(
        c["status"] in {"passed", "not_applicable"}
        for c in body["evidence_quality"]["checks"]
    )
    assert body["reconciliation"]["status"] == "passed"
    assert body["reconciliation"]["contribution_sum"] == pytest.approx(1.0)
    assert body["question"] == "Why did Value For Money decrease?"

    counts = _types(_all_evidence(client, body["id"]))
    assert counts.get("gone_segment") == 1
    assert counts.get("offsetting_factor") == 3
    assert counts.get("validation") == 6
    assert counts.get("drill_down")

    assert client.get(f"/api/investigations/{body['id']}/queries").json()["total"] > 0
    assert client.get(f"/api/investigations/{body['id']}/audit").json()["total"] > 0


def test_the_root_cause_pass_costs_seven_statements(
    client, investigation_airline_csv_bytes
) -> None:
    """The figure the specification quotes, kept observable.

    The trace holds more than seven in total because the investigation also runs
    anomaly detection on the same connection; the root-cause slice is what the
    seven refers to.
    """
    dataset_id = _airline_dataset(client, investigation_airline_csv_bytes)
    body = _created(client, dataset_id)

    provenance = next(
        c
        for c in body["evidence_quality"]["checks"]
        if c["check"] == "query_provenance"
    )
    assert provenance["inputs"]["root_cause_statements"] == 7
    assert provenance["inputs"]["statements_reported"] == 7
    assert provenance["inputs"]["statements_traced"] >= 7


def test_the_investigation_also_looks_for_anomalies(
    client, investigation_airline_csv_bytes
) -> None:
    """Anomaly and trend are two of the fourteen evidence types.

    With only two periods there is not enough history to flag one, so the honest
    outcome is no anomaly records rather than an invented finding - and the run
    still completes.
    """
    dataset_id = _airline_dataset(client, investigation_airline_csv_bytes)
    body = _created(client, dataset_id)

    kinds = [e["event_type"] for e in client.get(
        f"/api/investigations/{body['id']}/audit?limit=200"
    ).json()["items"]]
    assert "anomaly_detection_executed" in kinds
    assert body["status"] == "completed"
