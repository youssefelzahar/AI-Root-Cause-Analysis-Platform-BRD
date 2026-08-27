"""POST /api/ai/analyze, end to end through the real pipeline.

Datasets are created by uploading CSV bytes as every other integration test does;
profiling runs inline because the suite sets PROFILING_ASYNC=false. The language
model is the deterministic fake provider, set by AI_PROVIDER=fake in conftest - so
these tests exercise the whole path (understand, resolve, plan, execute, ground,
explain, verify) without a model installed and without a flaky assertion.

What is asserted here is never the prose. It is that the structured answer is
correct, that it is grounded in a persisted investigation, that the question is
recorded on that investigation, and that every way this can go wrong degrades
instead of failing: an ambiguous KPI asks, a missing model falls back to a
template, an unreachable segment reports why.
"""

import uuid

import pytest

from app.core.config import settings
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


def _ask(client, dataset_id: str, question: str, **body):
    return client.post(
        "/api/ai/analyze", json={"dataset_id": dataset_id, "question": question, **body}
    )


def _answered(client, dataset_id: str, question: str, **body) -> dict:
    response = _ask(client, dataset_id, question, **body)
    assert response.status_code == 200, response.text
    return response.json()


def _driver(payload: dict, value: str) -> dict:
    matched = [d for d in payload["drivers"] if d["value"] == value]
    assert matched, f"{value} is not among {[d['value'] for d in payload['drivers']]}"
    return matched[0]


# --- the golden scenario ------------------------------------------------------
# rca_golden.csv is the PRD's own example: revenue over region / product / segment,
# where only Cairo / Product A / Enterprise moves. test_rca_api pins the numbers;
# what matters here is that the AI answer carries those numbers and nothing else.


def test_a_question_produces_a_grounded_answer(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "Why did revenue decrease?")

    assert payload["status"] in {"completed", "partial"}
    assert payload["intent"] == "ROOT_CAUSE_ANALYSIS"
    assert payload["answer"]
    # The analysis it is grounded in is addressable.
    assert payload["investigation_id"]

    evidence = payload["evidence"]
    assert evidence["kpi_name"] == "Revenue"
    assert evidence["previous_value"] == pytest.approx(1500.0)
    assert evidence["current_value"] == pytest.approx(1200.0)
    assert evidence["absolute_change"] == pytest.approx(-300.0)
    assert evidence["percentage_change"] == pytest.approx(-20.0)
    assert evidence["direction"] == "down"


def test_the_named_driver_is_the_one_the_engine_ranked(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "Which region drove the revenue decline?")

    cairo = _driver(payload, "Cairo")
    assert cairo["dimension"] == "region"
    assert cairo["absolute_change"] == pytest.approx(-300.0)
    assert cairo["contribution_percentage"] == pytest.approx(100.0)
    assert cairo["classification"] == "primary"
    assert cairo["rank"] == 1


def test_every_driver_links_to_the_evidence_record_behind_it(
    client, rca_golden_csv_bytes
) -> None:
    """The claim in the prose has to be traceable to a row with its own provenance."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "Why did revenue decrease?")

    cairo = _driver(payload, "Cairo")
    assert cairo["evidence_id"]
    assert cairo["evidence_id"] in payload["evidence_ids"]

    fetched = client.get(f"/api/evidence/{cairo['evidence_id']}")
    assert fetched.status_code == 200, fetched.text
    record = fetched.json()
    assert record["dimension_value"] == "Cairo"
    # The record carries the statement that produced it, which is the whole chain:
    # question -> answer -> claim -> SQL.
    assert record["query"]


def test_the_drill_down_path_reaches_the_segment_the_engine_found(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "Why did revenue decrease?")

    path = payload["evidence"]["drill_path"]
    assert path, "the golden scenario has a three-level hierarchy"
    assert path[0] == "region Cairo"
    assert any("Enterprise" in step for step in path)


def test_the_question_is_recorded_on_the_investigation(
    client, db_session, rca_golden_csv_bytes
) -> None:
    """``Investigation.question`` existed and nothing set it until this layer."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "Why did revenue decrease?")

    row = db_session.get(Investigation, uuid.UUID(payload["investigation_id"]))
    assert row is not None
    assert row.question == "Why did revenue decrease?"


def test_an_answer_never_claims_causation(client, rca_golden_csv_bytes) -> None:
    """A contribution is arithmetic about a decomposition. Calling it a cause is the
    one claim this platform is built not to make."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "What caused the revenue drop?")
    assert "caused" not in (payload["answer"] or "").lower()


def test_the_steps_are_reported_for_the_progress_view(client, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "Why did revenue decrease?")

    tools = [step["tool"] for step in payload["steps"]]
    assert tools[0] == "get_kpi_result"
    assert "contribution_analysis" in tools
    assert all(step["ok"] for step in payload["steps"]), payload["steps"]


# --- multiple drivers ---------------------------------------------------------


def test_secondary_and_offsetting_segments_are_reported_separately(
    client, rca_drivers_csv_bytes
) -> None:
    """rca_drivers.csv has a primary, a secondary, an offsetting factor, a NEW
    segment and a GONE one - the shape a one-mover fixture cannot show."""
    dataset_id = _ready_dataset(
        client, rca_drivers_csv_bytes, dimensions=["region", "product", "channel"]
    )
    payload = _answered(client, dataset_id, "Why did revenue decrease?")

    assert _driver(payload, "Cairo")["classification"] == "primary"
    offsetting = {d["value"] for d in payload["offsetting_factors"]}
    assert offsetting, "this fixture has a segment moving against the KPI"
    # An offsetting factor is not a driver: the two lists must not overlap.
    assert offsetting.isdisjoint({d["value"] for d in payload["drivers"]})


def test_a_gone_segment_is_flagged_rather_than_shown_as_a_full_swing(
    client, rca_drivers_csv_bytes
) -> None:
    dataset_id = _ready_dataset(
        client, rca_drivers_csv_bytes, dimensions=["region", "product", "channel"]
    )
    payload = _answered(client, dataset_id, "Why did revenue decrease?")

    lifecycle = [
        d
        for d in payload["drivers"] + payload["offsetting_factors"]
        if d["is_new_segment"] or d["is_lost_segment"]
    ]
    assert lifecycle, "this fixture has both a new and a lost segment"


# --- periods the engine cannot target ----------------------------------------


def test_a_period_the_engine_did_not_analyse_is_reported_as_a_substitution(
    client, rca_golden_csv_bytes
) -> None:
    """Periods are anchored on the data's own latest timestamp, so a period named in
    a question is reported against rather than analysed directly. Saying so is the
    difference between a stated assumption and a wrong answer.

    The fixture holds June and July 2026, so January is genuinely outside both
    windows.
    """
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "Why did revenue decrease in January?")

    assert any("January" in note for note in payload["assumptions"]), payload["assumptions"]
    # And the periods reported are the engine's own, written as date ranges rather
    # than as the engine's internal "current" / "previous" role names.
    assert payload["evidence"]["current_period"].startswith("2026-")
    assert "exclusive" in payload["evidence"]["current_period"]


def test_a_period_that_really_was_analysed_adds_no_caveat(
    client, rca_golden_csv_bytes
) -> None:
    """The fixture's latest complete month is July 2026, so asking about July is
    asking about the window that was actually compared. A caveat here would be
    noise, and noise trains a reader to skip the caveats that matter."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "Why did revenue decrease in July?")

    assert "2026-07" in payload["evidence"]["current_period"]
    assert not any("You asked about" in note for note in payload["assumptions"])


def test_the_periods_are_written_as_windows_not_role_names(
    client, rca_golden_csv_bytes
) -> None:
    """The engine labels its periods "current" and "previous", which is useful
    inside the engine and useless in a sentence."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    evidence = _answered(client, dataset_id, "Why did revenue decrease?")["evidence"]

    assert evidence["previous_period"] not in {"previous", "current"}
    assert evidence["previous_period"].startswith("2026-06")
    assert evidence["current_period"].startswith("2026-07")


# --- clarification ------------------------------------------------------------


def test_an_ambiguous_kpi_asks_instead_of_guessing(client, rca_golden_csv_bytes) -> None:
    """Two definitions whose names both match "sales". Picking one would answer a
    different question with no way for a reader to tell."""
    upload = _upload(client, rca_golden_csv_bytes)
    dataset_id = upload.json()["dataset"]["id"]
    assert _define_kpi(client, dataset_id, name="Sales Revenue").status_code == 201
    # The second supersedes the first, but both rows remain.
    assert _define_kpi(client, dataset_id, name="Sales Volume").status_code == 201

    payload = _answered(client, dataset_id, "Why did sales drop?")
    assert payload["status"] == "clarification"
    assert payload["clarification"]["code"] == "AMBIGUOUS_KPI"
    assert set(payload["clarification"]["options"]) == {"Sales Revenue", "Sales Volume"}
    # Nothing ran, so nothing was persisted.
    assert payload["investigation_id"] is None
    assert payload["drivers"] == []


def test_a_clarification_creates_no_investigation(
    client, db_session, rca_golden_csv_bytes
) -> None:
    upload = _upload(client, rca_golden_csv_bytes)
    dataset_id = upload.json()["dataset"]["id"]
    _define_kpi(client, dataset_id, name="Sales Revenue")
    _define_kpi(client, dataset_id, name="Sales Volume")

    _answered(client, dataset_id, "Why did sales drop?")
    assert db_session.query(Investigation).count() == 0


def test_a_hint_matching_no_kpi_answers_the_configured_one_and_says_so(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    payload = _answered(client, dataset_id, "Why did margin decrease?")

    assert payload["status"] in {"completed", "partial"}
    assert payload["evidence"]["kpi_name"] == "Revenue"
    assert any("margin" in note.lower() for note in payload["assumptions"])


def test_a_dataset_with_no_kpi_is_rejected_before_anything_runs(
    client, rca_golden_csv_bytes
) -> None:
    upload = _upload(client, rca_golden_csv_bytes)
    dataset_id = upload.json()["dataset"]["id"]

    response = _ask(client, dataset_id, "Why did revenue decrease?")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_ANALYSIS_READY"


# --- follow-up questions ------------------------------------------------------


def test_a_follow_up_reuses_the_investigation_rather_than_recomputing(
    client, db_session, rca_golden_csv_bytes
) -> None:
    """The tree and every evidence record are persisted, so continuing a
    conversation is a read - re-running the engine would spend a DuckDB pass to
    arrive at identical numbers."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    first = _answered(client, dataset_id, "Why did revenue decrease?")
    before = db_session.query(Investigation).count()

    follow_up = _answered(
        client,
        dataset_id,
        "What happened in Cairo?",
        investigation_id=first["investigation_id"],
    )
    assert follow_up["investigation_id"] == first["investigation_id"]
    assert db_session.query(Investigation).count() == before


def test_a_follow_up_about_a_segment_uses_the_existing_tree(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    first = _answered(client, dataset_id, "Why did revenue decrease?")

    follow_up = _answered(
        client,
        dataset_id,
        "What happened in Cairo?",
        investigation_id=first["investigation_id"],
    )
    assert follow_up["intent"] in {"DRILL_DOWN", "FOLLOW_UP_ANALYSIS"}
    assert follow_up["evidence"]["drill_path"]
    assert any("Cairo" in step for step in follow_up["evidence"]["drill_path"])


def test_a_follow_up_about_a_segment_that_was_never_expanded_says_so(
    client, rca_golden_csv_bytes
) -> None:
    """Only the winning dimension's material segments were expanded, so an honest
    "not in the hierarchy" is the right answer rather than an invented breakdown."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    first = _answered(client, dataset_id, "Why did revenue decrease?")

    follow_up = _answered(
        client,
        dataset_id,
        "What happened in Alexandria?",
        investigation_id=first["investigation_id"],
    )
    assert follow_up["status"] == "partial"
    assert any("Alexandria" in note for note in follow_up["limitations"])


def test_an_investigation_from_another_dataset_is_not_accepted(
    client, rca_golden_csv_bytes, rca_drivers_csv_bytes
) -> None:
    first_dataset = _ready_dataset(client, rca_golden_csv_bytes)
    other_dataset = _ready_dataset(
        client, rca_drivers_csv_bytes, dimensions=["region", "product", "channel"]
    )
    answered = _answered(client, first_dataset, "Why did revenue decrease?")

    response = _ask(
        client,
        other_dataset,
        "Why did revenue decrease?",
        investigation_id=answered["investigation_id"],
    )
    assert response.status_code == 404, response.text


# --- an unavailable model -----------------------------------------------------


def test_an_unavailable_model_still_returns_the_analysis(
    client, monkeypatch, rca_golden_csv_bytes
) -> None:
    """The requirement this layer is shaped around: the investigation is the
    valuable part and it must survive a missing LLM."""
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)

    from app.ai.providers import reset_ai_cache

    monkeypatch.setattr(settings, "ai_provider", "ollama")
    # A port nothing is listening on, so every call fails as LLM_UNAVAILABLE.
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "ai_intent_retries", 0)
    reset_ai_cache()
    try:
        payload = _answered(client, dataset_id, "Why did revenue decrease?")
    finally:
        reset_ai_cache()

    assert payload["status"] == "partial"
    # The prose was assembled from the evidence, and the response says so.
    assert payload["answer_is_template"] is True
    assert payload["answer"]
    # Every structured field survived.
    assert payload["evidence"]["absolute_change"] == pytest.approx(-300.0)
    assert _driver(payload, "Cairo")["contribution_percentage"] == pytest.approx(100.0)
    assert payload["investigation_id"]


def test_a_disabled_ai_layer_is_a_typed_refusal(client, monkeypatch, rca_golden_csv_bytes) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    monkeypatch.setattr(settings, "ai_enabled", False)

    response = _ask(client, dataset_id, "Why did revenue decrease?")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AI_DISABLED"


# --- anomaly questions --------------------------------------------------------


def test_an_anomaly_question_runs_the_detector(client, anomaly_golden_csv_bytes) -> None:
    """The one tool that is a real second computation rather than a projection."""
    dataset_id = _ready_dataset(
        client,
        anomaly_golden_csv_bytes,
        dimensions=["region"],
    )
    payload = _answered(client, dataset_id, "Was there anything unusual about revenue?")

    assert payload["intent"] == "ANOMALY_ANALYSIS"
    tools = [step["tool"] for step in payload["steps"]]
    assert "detect_anomaly" in tools


# --- tenant isolation ---------------------------------------------------------


def test_another_company_cannot_ask_about_this_dataset(
    client, other_company, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    response = client.post(
        "/api/ai/analyze",
        json={"dataset_id": dataset_id, "question": "Why did revenue decrease?"},
        headers={"X-Company-Id": str(other_company)},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


# --- the supporting endpoints -------------------------------------------------


def test_health_is_always_200_so_the_page_can_render_the_state(client) -> None:
    response = client.get("/api/ai/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["enabled"] is True
    assert body["provider"] == "fake"


def test_health_reports_a_disabled_layer_without_erroring(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_enabled", False)
    body = client.get("/api/ai/health").json()
    assert body["ok"] is False
    assert body["error_code"] == "AI_DISABLED"


def test_the_tool_registry_is_published_and_holds_no_write_operation(client) -> None:
    response = client.get("/api/ai/tools")
    assert response.status_code == 200, response.text
    names = {tool["name"] for tool in response.json()}
    assert "get_kpi_result" in names
    assert "contribution_analysis" in names
    for name in names:
        assert not any(word in name for word in ("delete", "create", "sql", "execute"))


def test_a_question_that_is_too_short_is_rejected_by_the_contract(
    client, rca_golden_csv_bytes
) -> None:
    dataset_id = _ready_dataset(client, rca_golden_csv_bytes)
    response = _ask(client, dataset_id, "?")
    assert response.status_code == 422, response.text


def test_a_missing_dataset_is_a_404(client) -> None:
    response = _ask(client, "00000000-0000-0000-0000-0000000000ff", "Why did revenue drop?")
    assert response.status_code == 404, response.text
