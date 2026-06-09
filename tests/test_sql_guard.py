from dbaide.validation import SQLGuard
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ColumnInfo, TableInfo
from dbaide.validation import SchemaGuard


def test_sql_guard_rejects_write_statement():
    result = SQLGuard().validate("DROP TABLE users")
    assert not result.ok
    assert any(issue.code == "READONLY_ONLY" for issue in result.issues)


def test_sql_guard_rejects_multi_statement():
    result = SQLGuard().validate("SELECT 1; SELECT 2")
    assert not result.ok
    assert any(issue.code == "MULTI_STATEMENT" for issue in result.issues)


def test_sql_guard_adds_limit():
    result = SQLGuard(default_limit=25).validate("SELECT * FROM users")
    assert result.ok
    assert result.normalized_sql.endswith("LIMIT 25")


def test_schema_guard_allows_cte_refs_and_quoted_qualified_tables():
    context = DisclosureContext()
    context.record_tables([TableInfo(name="orders")], instance="local", database="main")
    context.record_columns("orders", [ColumnInfo(name="id")], instance="local", database="main")

    result = SchemaGuard().validate('WITH recent AS (SELECT * FROM "main"."orders") SELECT * FROM recent', context)

    assert result.ok


# -- dialect-aware backslash handling in string stripping --------------------

def test_strip_strings_backslash_generic_dialect():
    """In non-MySQL dialects backslash is a literal character inside strings.
    The parser must not treat it as an escape and must close the quote normally."""
    from dbaide.validation.sql_guard import _strip_strings_and_comments

    sql = r"SELECT 1 WHERE x = '\' AND y = 1"
    stripped = _strip_strings_and_comments(sql, dialect="generic")
    # The string '\' should be fully stripped; AND y = 1 must remain visible.
    assert "AND" in stripped.upper()


def test_strip_strings_backslash_mysql_dialect():
    """MySQL treats backslash as an escape; \\' is an escaped quote, so the
    string continues past it."""
    from dbaide.validation.sql_guard import _strip_strings_and_comments

    sql = r"SELECT 1 WHERE x = '\' AND y = 1"
    stripped = _strip_strings_and_comments(sql, dialect="mysql")
    # In MySQL, \' escapes the quote, so AND is inside the string.
    assert "AND" not in stripped.upper()


def test_multi_statement_detection_postgres_backslash():
    """A SQL string ending with backslash must not hide a subsequent semicolon
    in PostgreSQL mode."""
    guard = SQLGuard(dialect="postgres")
    sql = r"SELECT * FROM t WHERE x = '\'; DROP TABLE t; --'"
    result = guard.validate(sql)
    # Should detect multi-statement (the semicolon after the closing quote)
    assert any(issue.code in ("MULTI_STATEMENT", "READONLY_ONLY", "FORBIDDEN_KEYWORD") for issue in result.issues)
