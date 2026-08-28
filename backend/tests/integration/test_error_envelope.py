"""The error envelope, through the real handler stack.

Every error response is meant to have the shape
``{"error": {"code", "message", "details"}}`` so the frontend has one thing to
parse. These assert that it survives the handlers rather than only that the
exception classes produce it.

The 5xx cases are the ones worth pinning. They were broken: the handler logged
``extra={"message": ...}``, and ``logging.makeRecord`` reserves that key and
raises ``KeyError`` on it - so the AppError handler crashed, fell through to the
catch-all, and every typed 502 or 504 arrived as a generic 500 reading "An
unexpected error occurred." The code and the driver's own message were discarded
at exactly the moment a reader needed them.
"""

import pytest
from fastapi import APIRouter

from app.core.exceptions import (
    AppError,
    NotFoundError,
    UpstreamError,
    UpstreamTimeoutError,
    ValidationError,
)
from app.main import app

# Mounted once for these tests. Each route raises one shape of AppError, which is
# the only way to exercise the handler itself rather than a service that happens
# to raise.
_router = APIRouter(prefix="/_test_errors")


@_router.get("/upstream")
def _raise_upstream() -> None:
    raise UpstreamError(
        "The query failed: Invalid object name 'Widgets'.", code="QUERY_FAILED"
    )


@_router.get("/timeout")
def _raise_timeout() -> None:
    raise UpstreamTimeoutError("Connection timed out.", code="CONNECT_TIMEOUT")


@_router.get("/driver")
def _raise_driver() -> None:
    raise AppError(
        "The SQL Server driver is not installed.",
        code="SQLSERVER_DRIVER_UNAVAILABLE",
        status_code=500,
        details={"installed": []},
    )


@_router.get("/not-found")
def _raise_not_found() -> None:
    raise NotFoundError("Nothing here.", code="WIDGET_NOT_FOUND")


@_router.get("/invalid")
def _raise_validation() -> None:
    raise ValidationError("Bad filter.", code="INVALID_FILTER", details={"field": "type"})


@pytest.fixture
def captured_errors():
    """Records emitted by the exception handlers' logger.

    A handler attached here rather than pytest's ``caplog``, so the test does not
    depend on the logging plugin being loaded - and because the thing under test is
    precisely whether *building* the record raises.
    """
    import logging

    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Collector(level=logging.ERROR)
    logger = logging.getLogger("app.core.exceptions")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


@pytest.fixture(autouse=True, scope="module")
def _mount_test_routes():
    app.include_router(_router)
    yield
    # Leave the app as it was found, or later modules see these routes.
    app.router.routes = [
        route
        for route in app.router.routes
        if not getattr(route, "path", "").startswith("/_test_errors")
    ]


# --- 5xx: the regression this file exists for ---------------------------------


def test_an_upstream_error_keeps_its_code_and_message(client) -> None:
    """A 502 must arrive as a 502 naming what failed.

    Before the logging fix this was a 500 reading "An unexpected error occurred",
    which is how "Invalid object name 'Widgets'" became invisible in the SQL editor.
    """
    response = client.get("/_test_errors/upstream")

    assert response.status_code == 502, response.text
    body = response.json()["error"]
    assert body["code"] == "QUERY_FAILED"
    assert "Invalid object name" in body["message"]


def test_an_upstream_timeout_keeps_its_code(client) -> None:
    response = client.get("/_test_errors/timeout")

    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "CONNECT_TIMEOUT"


def test_a_500_app_error_keeps_its_code_and_details(client) -> None:
    """The driver-unavailable case, which names the fix in its message - so losing
    it to a generic 500 would leave a user with nothing to act on."""
    response = client.get("/_test_errors/driver")

    assert response.status_code == 500, response.text
    body = response.json()["error"]
    assert body["code"] == "SQLSERVER_DRIVER_UNAVAILABLE"
    assert "driver is not installed" in body["message"]
    assert body["details"] == {"installed": []}


def test_a_5xx_is_logged_without_crashing_the_handler(client, captured_errors) -> None:
    """The mechanism, asserted directly: `extra` must not use a reserved
    LogRecord key, or building the record raises and the handler is lost."""
    response = client.get("/_test_errors/upstream")

    assert response.status_code == 502
    logged = [r for r in captured_errors if r.getMessage() == "app_error"]
    assert logged, "a 5xx must still be logged"
    assert logged[0].code == "QUERY_FAILED"
    # The message travels under a non-reserved key.
    assert "Invalid object name" in logged[0].error


# --- 4xx: unchanged, and worth holding in place -------------------------------


def test_a_4xx_is_not_logged_as_an_error(client, captured_errors) -> None:
    """Only 5xx is logged. A 404 is an ordinary outcome, not an incident."""
    response = client.get("/_test_errors/not-found")

    assert response.status_code == 404
    assert [r for r in captured_errors if r.getMessage() == "app_error"] == []


def test_a_not_found_carries_its_own_code(client) -> None:
    body = client.get("/_test_errors/not-found").json()["error"]
    assert body["code"] == "WIDGET_NOT_FOUND"


def test_a_validation_error_carries_its_details(client) -> None:
    response = client.get("/_test_errors/invalid")

    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "INVALID_FILTER"
    assert body["details"] == {"field": "type"}


def test_details_are_omitted_when_there_are_none(client) -> None:
    """``to_payload`` drops the key rather than sending null, so a client can test
    for presence."""
    body = client.get("/_test_errors/timeout").json()["error"]
    assert "details" not in body
