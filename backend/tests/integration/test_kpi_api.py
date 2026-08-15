"""KPI detection, selection, and the Analysis Ready transition (PRD section 11)."""


def _upload(client, content: bytes, filename: str = "sales.csv"):
    return client.post("/api/uploads", files={"file": (filename, content, "text/csv")})


def test_candidates_match_the_prd_example(client, clean_csv_bytes) -> None:
    dataset_id = _upload(client, clean_csv_bytes).json()["dataset"]["id"]
    candidates = client.get(f"/api/datasets/{dataset_id}/kpi-candidates").json()

    measures = {c["column"] for c in candidates["measures"]}
    times = {c["column"] for c in candidates["time_columns"]}
    dimensions = [c["column"] for c in candidates["dimensions"]]

    assert {"revenue", "cost", "quantity"} <= measures
    assert times == {"date"}
    assert {"region", "product", "customer"} <= set(dimensions)

    default = candidates["recommended_default"]
    assert default["time_column"] == "date"
    assert default["aggregation"] == "SUM"
    # Every recommendation carries its reasoning (PRD principle 6).
    assert all(c["reasons"] for c in candidates["measures"])


def test_creating_a_definition_marks_the_dataset_analysis_ready(client, clean_csv_bytes) -> None:
    dataset_id = _upload(client, clean_csv_bytes).json()["dataset"]["id"]

    response = client.post(
        f"/api/datasets/{dataset_id}/kpi-definitions",
        json={
            "name": "Revenue",
            "column": "revenue",
            "aggregation": "SUM",
            "time_column": "date",
            "dimensions": ["region", "product", "customer"],
            "comparison": "previous_period",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["analysis_ready"] is True

    # The stored contract must match the PRD section 11 shape exactly.
    definition = body["kpi_definition"]["definition"]
    assert definition == {
        "name": "Revenue",
        "column": "revenue",
        "aggregation": "SUM",
        "time_column": "date",
        "dimensions": ["region", "product", "customer"],
        "comparison": "previous_period",
    }

    dataset = client.get(f"/api/datasets/{dataset_id}").json()
    assert dataset["status"] == "analysis_ready"
    assert dataset["analysis_ready"] is True


def test_creating_a_definition_without_time_column_is_allowed(client) -> None:
    dataset_id = _upload(
        client,
        b"region,product,revenue\nCairo,A,1200\nGiza,B,950\nCairo,B,1100\n",
        "sales_without_dates.csv",
    ).json()["dataset"]["id"]

    response = client.post(
        f"/api/datasets/{dataset_id}/kpi-definitions",
        json={
            "name": "Revenue",
            "column": "revenue",
            "aggregation": "SUM",
            "time_column": None,
            "dimensions": ["region", "product"],
            "comparison": "previous_period",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["analysis_ready"] is True
    assert body["kpi_definition"]["time_column"] is None
    assert body["validation"]["state"] == "warning"
    assert "TIME_COLUMN_NOT_SELECTED" in {i["code"] for i in body["validation"]["issues"]}


def test_non_numeric_kpi_column_is_rejected(client, clean_csv_bytes) -> None:
    dataset_id = _upload(client, clean_csv_bytes).json()["dataset"]["id"]

    response = client.post(
        f"/api/datasets/{dataset_id}/kpi-definitions",
        json={
            "name": "Bad",
            "column": "region",
            "aggregation": "SUM",
            "time_column": "date",
            "dimensions": ["product"],
            "comparison": "previous_period",
        },
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "KPI_VALIDATION_BLOCKED"
    assert "KPI_COLUMN_NOT_NUMERIC" in {i["code"] for i in error["details"]["issues"]}


def test_missing_column_is_rejected(client, clean_csv_bytes) -> None:
    dataset_id = _upload(client, clean_csv_bytes).json()["dataset"]["id"]
    response = client.post(
        f"/api/datasets/{dataset_id}/kpi-definitions",
        json={
            "name": "Ghost",
            "column": "does_not_exist",
            "aggregation": "SUM",
            "time_column": "date",
            "dimensions": [],
            "comparison": "previous_period",
        },
    )
    assert response.status_code == 422
    assert "KPI_COLUMN_MISSING" in {
        i["code"] for i in response.json()["error"]["details"]["issues"]
    }


def test_second_definition_deactivates_the_first(client, clean_csv_bytes) -> None:
    dataset_id = _upload(client, clean_csv_bytes).json()["dataset"]["id"]
    payload = {
        "name": "Revenue",
        "column": "revenue",
        "aggregation": "SUM",
        "time_column": "date",
        "dimensions": ["region"],
        "comparison": "previous_period",
    }
    client.post(f"/api/datasets/{dataset_id}/kpi-definitions", json=payload)
    second = client.post(
        f"/api/datasets/{dataset_id}/kpi-definitions", json={**payload, "name": "Cost", "column": "cost"}
    ).json()

    active = client.get(f"/api/datasets/{dataset_id}/kpi-definitions/active").json()
    assert active["id"] == second["kpi_definition"]["id"]
    assert active["column_name"] == "cost"

    listed = client.get(f"/api/datasets/{dataset_id}/kpi-definitions").json()["items"]
    assert sum(1 for item in listed if item["is_active"]) == 1


def test_deleting_the_active_definition_reverts_to_profiled(client, clean_csv_bytes) -> None:
    dataset_id = _upload(client, clean_csv_bytes).json()["dataset"]["id"]
    created = client.post(
        f"/api/datasets/{dataset_id}/kpi-definitions",
        json={
            "name": "Revenue",
            "column": "revenue",
            "aggregation": "SUM",
            "time_column": "date",
            "dimensions": ["region"],
            "comparison": "previous_period",
        },
    ).json()["kpi_definition"]

    assert client.delete(
        f"/api/datasets/{dataset_id}/kpi-definitions/{created['id']}"
    ).status_code == 204
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "profiled"
