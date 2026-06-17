from dbaide.adapters.base import DatabaseAdapter, quote_identifier, rows_to_result
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ColumnInfo, ColumnProfile, ConnectionConfig, TableInfo
from dbaide.tools import QueryTools


def test_validate_sql_report_flags_select_star_without_where():
    adapter = ExplainSpyAdapter(ConnectionConfig(name="local", type="sqlite", path="/tmp/test.db"))
    query = QueryTools(adapter, DisclosureContext())
    report = query.validate_sql_report("SELECT * FROM users")
    assert report.ok is True
    assert report.risk_level in {"low", "medium"}
    assert any("SELECT *" in warning or "large result" in warning.lower() for warning in report.warnings)


def test_explain_sql_does_not_double_prefix_explicit_explain():
    adapter = ExplainSpyAdapter(ConnectionConfig(name="local", type="sqlite", path="/tmp/test.db"))
    query = QueryTools(adapter, DisclosureContext())

    query.explain_sql("EXPLAIN SELECT * FROM users")

    assert adapter.explained_sql == "SELECT * FROM users"


def test_quote_identifier_handles_qualified_names():
    assert quote_identifier("public.orders", "postgres") == '"public"."orders"'
    assert quote_identifier("shop.orders", "mysql") == "`shop`.`orders`"


def test_explain_sql_rejects_undisclosed_table():
    """explain_sql must enforce the schema guard — same boundary as execute_sql.
    Without this, the LLM could probe for undisclosed tables via EXPLAIN."""
    import pytest
    ctx = DisclosureContext()
    ctx.record_tables([TableInfo(name="orders")], instance="local", database="main")
    adapter = ExplainSpyAdapter(ConnectionConfig(name="local", type="sqlite", path="/tmp/test.db"))
    query = QueryTools(adapter, ctx)

    # Known table → succeeds
    query.explain_sql("SELECT * FROM orders")
    assert "orders" in adapter.explained_sql

    # Undisclosed table → rejected
    with pytest.raises(ValueError, match="undisclosed"):
        query.explain_sql("SELECT * FROM secret_table")


def test_mysql_validation_rejects_full_outer_join():
    adapter = ExplainSpyAdapter(ConnectionConfig(name="shop", type="mysql"))
    adapter.dialect = "mysql"
    query = QueryTools(adapter, DisclosureContext())

    report = query.validate_sql_report("SELECT * FROM a FULL OUTER JOIN b ON a.id = b.id")

    assert report.ok is False
    assert any("FULL OUTER JOIN" in issue for issue in report.issues)


def test_execute_sql_uses_configured_default_limit_when_omitted():
    ctx = DisclosureContext()
    ctx.record_tables([TableInfo(name="users")], instance="local", database="main")
    adapter = ExplainSpyAdapter(ConnectionConfig(name="local", type="sqlite", path="/tmp/test.db"))
    query = QueryTools(adapter, ctx, default_limit=17)

    query.execute_sql("SELECT id FROM users", database="main")

    assert adapter.executed_sql.endswith("LIMIT 17")
    assert adapter.executed_limit == 17


class ExplainSpyAdapter(DatabaseAdapter):
    dialect = "sqlite"

    def __init__(self, config):
        super().__init__(config)
        self.explained_sql = ""
        self.executed_sql = ""
        self.executed_limit = None

    def test(self) -> None:
        return None

    def list_databases(self) -> list[str]:
        return ["main"]

    def list_tables(self, database: str = "") -> list[TableInfo]:
        return []

    def describe_table(self, table: str, database: str = "") -> list[ColumnInfo]:
        return []

    def _execute_readonly_impl(self, sql: str, *, database: str = "", limit: int | None = None, timeout_seconds: int = 10):
        self.executed_sql = sql
        self.executed_limit = limit
        return rows_to_result([], sql=sql)

    def explain(self, sql: str, *, database: str = "", timeout_seconds: int = 10):
        self.explained_sql = sql
        return rows_to_result([], sql="EXPLAIN " + sql)

    def sample_rows(self, table: str, *, database: str = "", limit: int = 20):
        return rows_to_result([], sql="")

    def profile_column(self, table: str, column: str, *, database: str = "", top_k: int = 10,
                       timeout_seconds: int = 30, **kwargs) -> ColumnProfile:
        return ColumnProfile(table=table, column=column, row_count=0, null_count=0)
