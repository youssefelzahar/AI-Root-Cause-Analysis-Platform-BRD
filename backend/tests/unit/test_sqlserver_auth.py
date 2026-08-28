"""The connector's two authentication paths.

No database, no network, no SQL Server: these assert on the ODBC string the
connector builds and on how the driver choice is made. That is the whole surface
the split introduced - everything past ``connect`` is plain DBAPI 2.0 and shared.

Why the split exists, since it is the kind of thing that looks like duplication:
``pymssql`` cannot do Windows authentication. It is FreeTDS-based and rejects a
``trusted_connection`` argument outright, so integrated auth needs ``pyodbc``.
Keeping both means the existing path is unchanged and the Linux image still works.
"""

import pytest

from app.connectors import sqlserver
from app.connectors.sqlserver import (
    ConnectionParams,
    DriverUnavailableError,
    _installed_odbc_driver,
    _odbc_connection_string,
    _type_code,
)
from app.db.models.enums import SqlAuthMode


def _windows(**overrides) -> ConnectionParams:
    values = {
        "host": "sqlserver.internal",
        "port": 1433,
        "database": "Sales",
        "auth_mode": SqlAuthMode.WINDOWS.value,
    }
    values.update(overrides)
    return ConnectionParams(**values)


class FakeOdbc:
    """Just enough pyodbc to exercise driver selection."""

    def __init__(self, drivers: list[str]) -> None:
        self._drivers = drivers

    def drivers(self) -> list[str]:
        return list(self._drivers)


# --- the mode flag ------------------------------------------------------------


def test_sql_auth_is_the_default() -> None:
    """So every caller that predates the split keeps its behaviour."""
    params = ConnectionParams(host="h", port=1433, database="d", username="u", password="p")
    assert params.auth_mode == SqlAuthMode.SQL.value
    assert not params.is_windows_auth


def test_a_windows_connection_carries_no_password() -> None:
    params = _windows()
    assert params.is_windows_auth
    assert params.password is None
    assert params.username == ""


# --- the ODBC connection string -----------------------------------------------


def test_trusted_connection_is_what_makes_it_integrated_auth() -> None:
    text = _odbc_connection_string(_windows(), "ODBC Driver 18 for SQL Server")
    assert "Trusted_Connection=yes" in text


def test_no_credential_appears_in_the_connection_string() -> None:
    """There is nothing to leak, and this is what keeps it that way."""
    text = _odbc_connection_string(_windows(), "ODBC Driver 18 for SQL Server")
    lowered = text.lower()
    assert "pwd=" not in lowered
    assert "password" not in lowered
    assert "uid=" not in lowered


def test_the_server_carries_the_port() -> None:
    text = _odbc_connection_string(_windows(port=1434), "ODBC Driver 18 for SQL Server")
    assert "SERVER=sqlserver.internal,1434" in text


def test_the_driver_name_is_braced() -> None:
    """ODBC driver names contain spaces; unbraced, the string does not parse."""
    text = _odbc_connection_string(_windows(), "ODBC Driver 18 for SQL Server")
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in text


@pytest.mark.parametrize(
    ("encrypt", "trust", "expected"),
    [
        (True, False, ["Encrypt=yes", "TrustServerCertificate=no"]),
        (False, True, ["Encrypt=no", "TrustServerCertificate=yes"]),
    ],
)
def test_the_tls_options_are_carried_through(encrypt, trust, expected) -> None:
    text = _odbc_connection_string(
        _windows(encrypt=encrypt, trust_server_certificate=trust),
        "ODBC Driver 18 for SQL Server",
    )
    for fragment in expected:
        assert fragment in text


def test_the_login_timeout_is_carried_through() -> None:
    text = _odbc_connection_string(_windows(login_timeout=7), "ODBC Driver 18 for SQL Server")
    assert "Connection Timeout=7" in text


# --- choosing an installed driver ---------------------------------------------


def test_the_newest_installed_driver_wins() -> None:
    chosen = _installed_odbc_driver(
        FakeOdbc(["SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"])
    )
    assert chosen == "ODBC Driver 18 for SQL Server"


def test_an_older_driver_is_accepted_when_it_is_all_there_is() -> None:
    """So an on-premise host works without being reconfigured first."""
    assert _installed_odbc_driver(FakeOdbc(["ODBC Driver 17 for SQL Server"])) == (
        "ODBC Driver 17 for SQL Server"
    )


def test_no_driver_at_all_names_the_fix() -> None:
    with pytest.raises(DriverUnavailableError) as caught:
        _installed_odbc_driver(FakeOdbc(["PostgreSQL ANSI"]))
    assert caught.value.code == "SQLSERVER_ODBC_DRIVER_MISSING"
    assert "ODBC Driver 18 for SQL Server" in caught.value.message
    assert caught.value.details["installed"] == ["PostgreSQL ANSI"]


def test_a_missing_pyodbc_is_a_typed_error_naming_the_alternative(monkeypatch) -> None:
    """The Linux-container case: no unixODBC, so the import itself fails. The
    message has to say what to do instead rather than surfacing an ImportError."""
    import builtins

    real_import = builtins.__import__

    def no_pyodbc(name, *args, **kwargs):
        if name == "pyodbc":
            raise ImportError("libodbc.so.2: cannot open shared object file")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyodbc)

    with pytest.raises(DriverUnavailableError) as caught:
        sqlserver._odbc_driver()
    assert caught.value.code == "SQLSERVER_ODBC_DRIVER_UNAVAILABLE"
    assert "SQL authentication" in caught.value.message


def test_a_typed_driver_error_is_not_rewrapped_as_a_connect_failure(monkeypatch) -> None:
    """A missing driver names its own fix, and CONNECT_FAILED would bury it."""
    def unavailable(params):
        raise DriverUnavailableError("no driver", code="SQLSERVER_ODBC_DRIVER_MISSING")

    monkeypatch.setattr(sqlserver, "_connect_windows_auth", unavailable)

    with pytest.raises(DriverUnavailableError) as caught:
        with sqlserver.connect(_windows()):
            pass
    assert caught.value.code == "SQLSERVER_ODBC_DRIVER_MISSING"


def test_a_windows_connection_never_reaches_pymssql(monkeypatch) -> None:
    """The whole point of the branch: pymssql cannot do this mode."""
    def explode(params):
        raise AssertionError("pymssql must not be used for Windows authentication")

    monkeypatch.setattr(sqlserver, "_connect_sql_auth", explode)
    monkeypatch.setattr(sqlserver, "_connect_windows_auth", lambda params: object())

    with sqlserver.connect(_windows()):
        pass


def test_a_sql_connection_never_reaches_pyodbc(monkeypatch) -> None:
    def explode(params):
        raise AssertionError("pyodbc must not be used for SQL authentication")

    monkeypatch.setattr(sqlserver, "_connect_windows_auth", explode)
    monkeypatch.setattr(sqlserver, "_connect_sql_auth", lambda params: object())

    params = ConnectionParams(host="h", port=1433, database="d", username="u", password="p")
    with sqlserver.connect(params):
        pass


# --- the one place the drivers disagree ---------------------------------------


def test_an_integer_type_code_is_kept() -> None:
    """pymssql reports one, and the wire contract is an optional int."""
    assert _type_code(3) == 3


def test_a_python_type_object_becomes_none() -> None:
    """pyodbc reports `str` where pymssql reports 1. Coercing it would produce a
    number that means nothing, so it is dropped instead."""
    assert _type_code(str) is None
    assert _type_code(None) is None
