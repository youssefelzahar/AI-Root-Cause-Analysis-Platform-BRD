"""Read-only SQL statement guard.

Parses with sqlglot rather than pattern-matching, because a regex over SQL text
is trivially defeated by comments, string literals and stacked statements.

This is a usability guard, not the security boundary. The real boundary is a
SQL login with only ``db_datareader``, plus the fact that every query runs in a
transaction that is always rolled back.
"""

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.core.config import settings

# Node types that write, or that can escape into writing.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Set,
    exp.Use,
    exp.Pragma,
    exp.Copy,
    # `SELECT * INTO new_table FROM x` parses as a Select but is a WRITE.
    # Missing this is the classic hole in guards like this one.
    exp.Into,
    # Anything sqlglot cannot classify: EXEC, BACKUP, DBCC, BULK INSERT...
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Except,
    exp.Intersect,
    exp.Subquery,
    exp.With,
)

FORBIDDEN_FUNCTIONS = {
    "xp_cmdshell",
    "sp_executesql",
    "sp_configure",
    "sp_addlogin",
    "sp_password",
    "openrowset",
    "opendatasource",
    "openquery",
    "openxml",
    "bulk",
}


@dataclass
class SqlGuardResult:
    allowed: bool
    statement_type: str | None = None
    reasons: list[str] = field(default_factory=list)
    normalized_sql: str | None = None


def _forbidden_name(name: str) -> bool:
    lowered = name.lower().strip("[]\"'")
    if lowered in FORBIDDEN_FUNCTIONS:
        return True
    return lowered.startswith(("xp_", "sp_"))


def check_sql(sql: str) -> SqlGuardResult:
    """Return whether ``sql`` is a single read-only statement."""
    if not sql or not sql.strip():
        return SqlGuardResult(False, None, ["The query is empty."])

    if len(sql) > settings.sql_max_statement_length:
        return SqlGuardResult(
            False,
            None,
            [f"The query exceeds {settings.sql_max_statement_length} characters."],
        )

    try:
        statements = [s for s in sqlglot.parse(sql, read="tsql") if s is not None]
    except Exception as exc:
        # Never execute a string the parser could not understand.
        return SqlGuardResult(False, None, [f"The query could not be parsed: {exc}"])

    if not statements:
        return SqlGuardResult(False, None, ["No executable statement was found."])
    if len(statements) > 1:
        return SqlGuardResult(
            False,
            None,
            ["Only a single statement may be executed. Remove the additional statements."],
        )

    root = statements[0]
    statement_type = type(root).__name__

    if not isinstance(root, ALLOWED_ROOTS):
        return SqlGuardResult(
            False,
            statement_type,
            [f"Only SELECT queries are allowed here. Detected: {statement_type.upper()}."],
        )

    reasons: list[str] = []
    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            name = type(node).__name__.upper()
            if isinstance(node, exp.Into):
                reasons.append("SELECT ... INTO writes a new table and is not allowed.")
            else:
                reasons.append(f"{name} is not allowed in a read-only query.")
        if isinstance(node, exp.Anonymous) and _forbidden_name(node.name or ""):
            reasons.append(f"The procedure or function '{node.name}' is not allowed.")
        if isinstance(node, exp.Table):
            table_name = node.name or ""
            if _forbidden_name(table_name):
                reasons.append(f"'{table_name}' is not allowed as a data source.")

    if reasons:
        return SqlGuardResult(False, statement_type, sorted(set(reasons)))

    try:
        normalized = root.sql(dialect="tsql")
    except Exception:
        normalized = None

    return SqlGuardResult(True, statement_type, [], normalized)
