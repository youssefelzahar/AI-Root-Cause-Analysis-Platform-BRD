"""Logging setup with secret redaction.

PRD principle 7 requires secrets to stay out of logs. Driver error strings in
particular tend to embed the full connection string including ``PWD=``.
"""

import logging
import re
import sys
from typing import Any

# Applied to every formatted message and to string values in `extra`.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Connection-string fragments: PWD=secret; / PASSWORD=secret;
    (re.compile(r"(?i)\b(pwd|password|passwd)\s*=\s*[^;,\s\"']+"), r"\1=***"),
    # JSON / dict style: "password": "secret"
    (
        re.compile(r"(?i)([\"']?(?:password|passwd|pwd|secret|token|api[_-]?key)[\"']?\s*:\s*)"
                   r"[\"'][^\"']*[\"']"),
        r"\1\"***\"",
    ),
    # Authorization headers - mask the scheme *and* the credential after it.
    (re.compile(r"(?i)(authorization\s*[:=]\s*).+"), r"\1***"),
)

_SENSITIVE_KEYS = re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization)")


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrubs secrets from the message and from any ``extra`` values."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub(k, v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._scrub(None, a) for a in record.args)
        for key, value in list(record.__dict__.items()):
            if key in logging.LogRecord("", 0, "", 0, "", None, None).__dict__:
                continue
            record.__dict__[key] = self._scrub(key, value)
        return True

    @staticmethod
    def _scrub(key: str | None, value: Any) -> Any:
        if key and _SENSITIVE_KEYS.search(key):
            return "***"
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, dict):
            return {k: RedactingFilter._scrub(k, v) for k, v in value.items()}
        return value


def configure_logging(
    level: str = "INFO", json_output: bool = False, echo_sql: bool = False
) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())

    if json_output:
        from pythonjsonlogger.json import JsonFormatter

        handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s :: %(message)s")
        )

    root.addHandler(handler)

    logging.getLogger("uvicorn.access").setLevel(max(logging.INFO, root.level))

    # SQLAlchemy's engine logger emits every statement *and its bound parameters*
    # at INFO, so leaving it at INFO is equivalent to echo=True: profiled column
    # values, customer names and connection ciphertext all end up in the log.
    # It must be gated on the explicit db_echo setting, never on the root level.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if echo_sql else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger
