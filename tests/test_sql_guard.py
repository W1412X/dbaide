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
