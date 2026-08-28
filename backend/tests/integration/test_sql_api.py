"""SQL connection and editor endpoints (PRD section 8).

Credential handling is the focus: the password must never appear in a
response, and must never be stored in plaintext.
"""

import json

from app.core.security import decrypt_secret
from app.db.models import SqlConnection

PASSWORD = "sup3r-s3cret-Passw0rd!"

PAYLOAD = {
    "name": "Warehouse",
    "host": "sqlserver.internal",
    "port": 1433,
    "database": "Sales",
    "username": "reader",
    "password": PASSWORD,
}


def _contains_password(node) -> bool:
    """Recursively hunt for the password anywhere in a response body."""
    if isinstance(node, str):
        return PASSWORD in node
    if isinstance(node, dict):
        return any(_contains_password(v) for v in node.values()) or any(
            "password" in str(k).lower() for k in node
        )
    if isinstance(node, list):
        return any(_contains_password(item) for item in node)
    return False


def test_created_connection_never_returns_a_password(client) -> None:
    response = client.post("/api/sql-connections", json=PAYLOAD)
    assert response.status_code == 201, response.text

    body = response.json()
    assert "password" not in body
    assert not _contains_password(body)
    assert PASSWORD not in json.dumps(body)


def test_password_is_encrypted_at_rest(client, db_session) -> None:
    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]
    record = db_session.get(SqlConnection, __import__("uuid").UUID(connection_id))

    assert record.password_encrypted != PASSWORD
    assert PASSWORD not in record.password_encrypted
    assert record.password_encrypted.startswith("v1:")
    # ...but it must still round-trip.
    assert decrypt_secret(record.password_encrypted) == PASSWORD


def test_listing_connections_never_leaks_credentials(client) -> None:
    client.post("/api/sql-connections", json=PAYLOAD)
    body = client.get("/api/sql-connections").json()
    assert PASSWORD not in json.dumps(body)


def test_updating_without_a_password_keeps_the_existing_one(client, db_session) -> None:
    import uuid as _uuid

    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]
    before = db_session.get(SqlConnection, _uuid.UUID(connection_id)).password_encrypted

    client.patch(f"/api/sql-connections/{connection_id}", json={"host": "new-host"})
    db_session.expire_all()
    after = db_session.get(SqlConnection, _uuid.UUID(connection_id))

    assert after.host == "new-host"
    assert after.password_encrypted == before


def test_duplicate_connection_name_conflicts(client) -> None:
    client.post("/api/sql-connections", json=PAYLOAD)
    assert client.post("/api/sql-connections", json=PAYLOAD).status_code == 409


def test_sql_validate_endpoint_reports_guard_decisions(client) -> None:
    allowed = client.post("/api/sql/validate", json={"sql": "SELECT * FROM t"}).json()
    assert allowed["allowed"] is True

    blocked = client.post("/api/sql/validate", json={"sql": "DROP TABLE t"}).json()
    assert blocked["allowed"] is False
    assert blocked["reasons"]


def test_write_statement_is_rejected_before_any_connection_is_opened(client, monkeypatch) -> None:
    """The guard must run first, so a rejected statement never reaches SQL Server."""
    from app.connectors import sqlserver

    def explode(*_args, **_kwargs):
        raise AssertionError("the connector must not be called for a rejected statement")

    monkeypatch.setattr(sqlserver, "connect", explode)
    monkeypatch.setattr(sqlserver, "run_query", explode)

    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]
    response = client.post(
        f"/api/sql/connections/{connection_id}/execute",
        json={"sql": "UPDATE sales SET revenue = 0"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "STATEMENT_NOT_READ_ONLY"


def test_execute_returns_rows_from_the_connector(client, monkeypatch) -> None:
    from app.connectors import sqlserver

    def fake_run_query(_params, _sql, *, row_limit, timeout_seconds):
        assert row_limit > 0 and timeout_seconds > 0
        return sqlserver.QueryResult(
            columns=[{"name": "region", "sql_type_code": 1}, {"name": "revenue", "sql_type_code": 3}],
            rows=[["Cairo", 1200], ["Giza", 980]],
            row_count=2,
            truncated=False,
            elapsed_ms=7,
        )

    monkeypatch.setattr(sqlserver, "run_query", fake_run_query)

    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]
    body = client.post(
        f"/api/sql/connections/{connection_id}/execute",
        json={"sql": "SELECT region, revenue FROM sales"},
    ).json()

    assert body["row_count"] == 2
    assert body["truncated"] is False
    assert [c["name"] for c in body["columns"]] == ["region", "revenue"]


def test_failed_connection_test_returns_200_with_ok_false(client, monkeypatch) -> None:
    """An unreachable server is a renderable outcome, not an API error."""
    from app.connectors import sqlserver

    monkeypatch.setattr(
        sqlserver,
        "test_connection",
        lambda _params: {"ok": False, "error_code": "CONNECT_FAILED", "message": "host unreachable"},
    )

    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]
    response = client.post(f"/api/sql-connections/{connection_id}/test")

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert PASSWORD not in json.dumps(response.json())


def test_save_query_as_dataset_produces_a_profiled_dataset(client, monkeypatch) -> None:
    """PRD section 8: query output becomes a first-class internal dataset."""
    from app.connectors import sqlserver

    def fake_iter_rows(_params, _sql, *, max_rows, timeout_seconds):
        columns = ["date", "region", "revenue"]
        yield columns, [
            ["2026-06-01", "Cairo", "1200"],
            ["2026-06-02", "Giza", "980"],
            ["2026-06-03", "Cairo", "1100"],
            ["2026-06-04", "Alexandria", "1310"],
        ]

    monkeypatch.setattr(sqlserver, "iter_rows", fake_iter_rows)

    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]
    response = client.post(
        f"/api/sql/connections/{connection_id}/save-as-dataset",
        json={"sql": "SELECT date, region, revenue FROM sales", "dataset_name": "Sales extract"},
    )
    assert response.status_code == 201, response.text

    dataset = response.json()
    assert dataset["source_type"] == "sqlserver"
    assert dataset["file_format"] == "parquet"
    # The SELECT text is provenance, not a secret.
    assert dataset["source_query"].startswith("SELECT")
    assert PASSWORD not in json.dumps(dataset)

    # It flows through the same profiling pipeline as an upload.
    profile = client.get(f"/api/datasets/{dataset['id']}/profile").json()
    assert profile["state"] == "ready"
    assert profile["profile"]["row_count"] == 4
    assert profile["profile"]["column_count"] == 3


# --- windows authentication ---------------------------------------------------
# The mode exists because pymssql cannot do it: it is FreeTDS-based and rejects a
# trusted_connection argument outright. So a Windows-auth connection takes the
# pyodbc path, and the invariant worth pinning is that it stores no credential at
# all - not an empty one.

WINDOWS_PAYLOAD = {
    "name": "Warehouse (integrated)",
    "host": "sqlserver.internal",
    "port": 1433,
    "database": "Sales",
    "auth_mode": "windows",
}


def test_a_windows_auth_connection_needs_no_password(client) -> None:
    response = client.post("/api/sql-connections", json=WINDOWS_PAYLOAD)
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["auth_mode"] == "windows"
    # No user to name: the login is whoever the server process runs as.
    assert body["username"] == ""


def test_a_windows_auth_connection_stores_no_credential(client, db_session) -> None:
    """NULL rather than an encrypted empty string, so "no password" and "the
    password is blank" stay distinguishable."""
    import uuid

    connection_id = client.post("/api/sql-connections", json=WINDOWS_PAYLOAD).json()["id"]
    record = db_session.get(SqlConnection, uuid.UUID(connection_id))

    assert record.password_encrypted is None
    assert record.auth_mode == "windows"


def test_sql_auth_still_requires_a_password(client) -> None:
    """The default mode is unchanged, so an omitted password is still a 422."""
    payload = {k: v for k, v in PAYLOAD.items() if k != "password"}
    response = client.post("/api/sql-connections", json=payload)
    assert response.status_code == 422, response.text


def test_sql_auth_still_requires_a_username(client) -> None:
    payload = {k: v for k, v in PAYLOAD.items() if k != "username"}
    response = client.post("/api/sql-connections", json=payload)
    assert response.status_code == 422, response.text


def test_a_password_sent_with_windows_auth_is_refused_not_ignored(client) -> None:
    """Silently dropping a credential someone believes is protecting something is
    worse than refusing the request."""
    response = client.post(
        "/api/sql-connections", json={**WINDOWS_PAYLOAD, "password": PASSWORD}
    )
    assert response.status_code == 422, response.text


def test_a_username_sent_with_windows_auth_is_refused(client) -> None:
    response = client.post(
        "/api/sql-connections", json={**WINDOWS_PAYLOAD, "username": "reader"}
    )
    assert response.status_code == 422, response.text


def test_a_password_cannot_be_patched_onto_a_windows_connection(client) -> None:
    """It would store a credential the connector never sends, and leave the row
    failing its own CHECK constraint."""
    connection_id = client.post("/api/sql-connections", json=WINDOWS_PAYLOAD).json()["id"]

    response = client.patch(
        f"/api/sql-connections/{connection_id}", json={"password": PASSWORD}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AUTH_MODE_TAKES_NO_PASSWORD"


def test_a_windows_connection_reaches_the_connector_without_a_password(
    client, monkeypatch
) -> None:
    """What the service hands the connector, which is where the two drivers part."""
    from app.connectors import sqlserver

    seen: dict = {}

    def fake_test(params):
        seen["auth_mode"] = params.auth_mode
        seen["password"] = params.password
        seen["is_windows_auth"] = params.is_windows_auth
        return {"ok": True, "server_version": "Microsoft SQL Server 2022"}

    monkeypatch.setattr(sqlserver, "test_connection", fake_test)

    connection_id = client.post("/api/sql-connections", json=WINDOWS_PAYLOAD).json()["id"]
    response = client.post(f"/api/sql-connections/{connection_id}/test")

    assert response.status_code == 200, response.text
    assert seen["auth_mode"] == "windows"
    assert seen["password"] is None
    assert seen["is_windows_auth"] is True


def test_an_existing_connection_defaults_to_sql_auth(client) -> None:
    """Migration 0004 defaults the column, so a client that never heard of
    auth_mode keeps working unchanged."""
    body = client.post("/api/sql-connections", json=PAYLOAD).json()
    assert body["auth_mode"] == "sql"


# --- deletion -----------------------------------------------------------------


def test_a_connection_can_be_deleted(client, db_session) -> None:
    import uuid

    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]

    response = client.delete(f"/api/sql-connections/{connection_id}")
    assert response.status_code == 204, response.text
    assert db_session.get(SqlConnection, uuid.UUID(connection_id)) is None


def test_deleting_a_connection_twice_is_a_404(client) -> None:
    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]
    assert client.delete(f"/api/sql-connections/{connection_id}").status_code == 204

    repeat = client.delete(f"/api/sql-connections/{connection_id}")
    assert repeat.status_code == 404, repeat.text
    assert repeat.json()["error"]["code"] == "CONNECTION_NOT_FOUND"


def test_another_company_cannot_delete_this_connection(client, other_company) -> None:
    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]

    response = client.delete(
        f"/api/sql-connections/{connection_id}",
        headers={"X-Company-Id": str(other_company)},
    )
    # 404, not 403: the API never confirms another tenant's row exists.
    assert response.status_code == 404, response.text


def test_deleting_a_connection_frees_its_name(client) -> None:
    """The name is unique per company, so a delete has to actually release it."""
    connection_id = client.post("/api/sql-connections", json=PAYLOAD).json()["id"]
    client.delete(f"/api/sql-connections/{connection_id}")

    again = client.post("/api/sql-connections", json=PAYLOAD)
    assert again.status_code == 201, again.text
