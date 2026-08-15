"""End-to-end coverage of the PRD section 21 Definition of Done."""

import uuid

from app.core.config import settings


def _upload(client, content: bytes, filename: str = "sales.csv"):
    return client.post(
        "/api/uploads",
        files={"file": (filename, content, "text/csv")},
    )


def test_upload_profiles_and_validates_a_csv(client, clean_csv_bytes) -> None:
    response = _upload(client, clean_csv_bytes)
    assert response.status_code == 201, response.text

    dataset = response.json()["dataset"]
    dataset_id = dataset["id"]

    # PRD section 6: the storage key must be UUID-based, not the filename.
    assert dataset["original_filename"] == "sales.csv"
    assert "sales" not in dataset["storage_key"]
    assert dataset_id in dataset["storage_key"]
    assert dataset["checksum_sha256"]
    assert dataset["size_bytes"] == len(clean_csv_bytes)

    # Profiling runs inline in tests, so it is already done.
    status = client.get(f"/api/datasets/{dataset_id}/status").json()
    assert status["status"] == "profiled"
    assert status["profile_ready"] is True
    assert status["row_count"] == 10
    assert status["column_count"] == 7

    profile = client.get(f"/api/datasets/{dataset_id}/profile").json()
    assert profile["state"] == "ready"
    assert profile["profile"]["row_count"] == 10
    assert profile["profile"]["duplicate_row_count"] == 0
    assert profile["profile"]["missing_cell_pct"] == 0.0

    columns = {c["column_name"]: c for c in profile["columns"]}
    assert columns["revenue"]["inferred_type"] in {"integer", "numeric"}
    assert columns["date"]["inferred_type"] == "date"
    assert columns["region"]["top_values"]
    assert columns["date"]["datetime_stats"]["detected_frequency"] == "daily"

    validation = client.get(f"/api/datasets/{dataset_id}/validation").json()
    assert validation["state"] in {"pass", "warning"}


def test_string_revenue_is_converted_not_rejected(client, messy_csv_bytes) -> None:
    """The PRD section 10 headline example."""
    dataset_id = _upload(client, messy_csv_bytes, "messy.csv").json()["dataset"]["id"]

    profile = client.get(f"/api/datasets/{dataset_id}/profile").json()
    revenue = next(c for c in profile["columns"] if c["column_name"] == "revenue")

    assert revenue["inferred_type"] == "numeric", "should convert rather than stay text"
    assert revenue["requires_conversion"] is True
    assert 0 < revenue["conversion_confidence"] < 1
    assert revenue["invalid_value_count"] == 1
    assert "not-a-number" in (revenue["sample_invalid_values"] or [])

    validation = client.get(f"/api/datasets/{dataset_id}/validation").json()
    assert validation["state"] == "warning", "convertible data must not block analysis"
    codes = {issue["code"] for issue in validation["issues"]}
    assert "LOSSY_TYPE_CONVERSION" in codes


def test_dataset_without_a_numeric_column_is_blocked(client, text_only_csv_bytes) -> None:
    dataset_id = _upload(client, text_only_csv_bytes, "text.csv").json()["dataset"]["id"]

    validation = client.get(f"/api/datasets/{dataset_id}/validation").json()
    assert validation["state"] == "blocked"
    assert "NO_NUMERIC_COLUMN" in {issue["code"] for issue in validation["issues"]}
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "blocked"


def test_oversized_upload_is_rejected_and_leaves_nothing_behind(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_upload_bytes", 512)

    response = _upload(client, b"a,b\n" + b"1,2\n" * 500, "big.csv")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"

    # No usable dataset row should survive the failure.
    items = client.get("/api/datasets").json()["items"]
    assert all(item["status"] != "profiled" for item in items)


def test_unsupported_file_type_is_rejected(client) -> None:
    response = client.post(
        "/api/uploads", files={"file": ("payload.exe", b"MZ\x00", "application/octet-stream")}
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_empty_file_is_rejected(client) -> None:
    assert _upload(client, b"", "empty.csv").status_code == 400


def test_hostile_filename_is_stored_safely_but_preserved_as_metadata(client, clean_csv_bytes) -> None:
    dataset = _upload(client, clean_csv_bytes, "../../../etc/passwd.csv").json()["dataset"]

    assert ".." not in dataset["storage_key"]
    assert "etc" not in dataset["storage_key"]
    # The original name is still reported back to the user as metadata.
    assert dataset["original_filename"] == "passwd.csv"


def test_delete_removes_the_row_and_the_stored_object(client, clean_csv_bytes, storage_root) -> None:
    dataset = _upload(client, clean_csv_bytes).json()["dataset"]
    key = dataset["storage_key"]
    assert (storage_root / key).exists()

    assert client.delete(f"/api/datasets/{dataset['id']}").status_code == 204
    assert not (storage_root / key).exists()
    assert client.get(f"/api/datasets/{dataset['id']}").status_code == 404


def test_preview_returns_rows(client, clean_csv_bytes) -> None:
    dataset_id = _upload(client, clean_csv_bytes).json()["dataset"]["id"]
    preview = client.get(f"/api/datasets/{dataset_id}/preview?limit=3").json()

    assert len(preview["rows"]) == 3
    assert [c["name"] for c in preview["columns"]][:2] == ["date", "region"]


def test_cross_company_access_returns_404_not_403(client, clean_csv_bytes, other_company) -> None:
    """Tenancy is enforced now so real auth can drop in without a rewrite."""
    dataset_id = _upload(client, clean_csv_bytes).json()["dataset"]["id"]

    response = client.get(
        f"/api/datasets/{dataset_id}", headers={"X-Company-Id": str(other_company)}
    )
    # 404, not 403: never confirm that another tenant's dataset exists.
    assert response.status_code == 404


def test_unknown_dataset_returns_404(client) -> None:
    assert client.get(f"/api/datasets/{uuid.uuid4()}").status_code == 404
