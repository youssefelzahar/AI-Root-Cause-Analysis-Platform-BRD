"""PRD principle 7: secrets must stay out of logs.

Driver errors in particular routinely embed the whole connection string.
"""

from app.core.logging import redact


def test_connection_string_password_is_redacted():
    assert "hunter2" not in redact("DRIVER={x};UID=sa;PWD=hunter2;Encrypt=yes")


def test_json_style_password_is_redacted():
    assert "hunter2" not in redact('{"username": "sa", "password": "hunter2"}')


def test_authorization_header_is_redacted():
    assert "abc123" not in redact("Authorization: Bearer abc123")


def test_ordinary_text_is_untouched():
    assert redact("profiled 1200 rows in 34ms") == "profiled 1200 rows in 34ms"
