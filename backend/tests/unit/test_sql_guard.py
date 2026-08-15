"""The read-only guard is the thing standing between the SQL editor and a
write, so its allow/block behaviour is table-driven and exhaustive."""

import pytest

from app.connectors.sql_guard import check_sql

ALLOWED = [
    "SELECT * FROM sales",
    "SELECT TOP 10 region, SUM(revenue) AS r FROM sales GROUP BY region ORDER BY r DESC",
    "WITH c AS (SELECT 1 AS x) SELECT * FROM c",
    "SELECT a FROM t1 UNION ALL SELECT b FROM t2",
    "SELECT * FROM (SELECT * FROM sales) q",
    "SELECT 'DROP TABLE users' AS note FROM t",  # keyword inside a literal
    "SELECT 1 --; DROP TABLE users",  # comment genuinely neutralises the rest
]

BLOCKED = [
    ("INSERT INTO t VALUES (1)", "insert"),
    ("UPDATE t SET x = 1", "update"),
    ("DELETE FROM t", "delete"),
    ("DROP TABLE t", "drop"),
    ("TRUNCATE TABLE t", "truncate"),
    ("ALTER TABLE t ADD c INT", "alter"),
    ("CREATE TABLE t (a INT)", "create"),
    ("SELECT * INTO t2 FROM t1", "select-into writes a table"),
    ("SELECT 1; DROP TABLE users", "stacked statements"),
    ("SELECT 1 --comment\n; DROP TABLE users", "stacked after a line comment"),
    ("SELECT 1 /* hidden */; DROP TABLE users", "stacked after a block comment"),
    ("EXEC sp_who", "stored procedure"),
    ("EXEC xp_cmdshell 'dir'", "shell execution"),
    ("SELECT * FROM OPENROWSET(BULK N'c:/x', SINGLE_CLOB) AS q", "openrowset"),
    ("USE master", "use"),
    ("WITH c AS (SELECT 1) INSERT INTO t SELECT * FROM c", "write hidden behind a CTE"),
    ("MERGE INTO t USING s ON 1=1 WHEN MATCHED THEN DELETE", "merge"),
    ("", "empty"),
    ("   ", "whitespace"),
    ("this is not sql at all !!!", "unparseable"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_read_only_queries_are_allowed(sql: str) -> None:
    result = check_sql(sql)
    assert result.allowed, f"should have been allowed: {sql} -> {result.reasons}"


@pytest.mark.parametrize("sql,description", BLOCKED)
def test_writes_and_stacked_statements_are_blocked(sql: str, description: str) -> None:
    result = check_sql(sql)
    assert not result.allowed, f"should have been blocked ({description}): {sql!r}"
    assert result.reasons, "a rejection must explain itself"


def test_overlong_statement_is_rejected() -> None:
    assert not check_sql("SELECT " + "a," * 20_000 + "b FROM t").allowed


def test_allowed_result_exposes_normalized_sql() -> None:
    result = check_sql("select a from t")
    assert result.allowed
    assert result.normalized_sql
    assert result.statement_type == "Select"
