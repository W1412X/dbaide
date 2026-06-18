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


def test_explain_sql_strips_explain_query_plan_prefix():
    adapter = ExplainSpyAdapter(ConnectionConfig(name="local", type="sqlite", path="/tmp/test.db"))
    query = QueryTools(adapter, DisclosureContext())

    query.explain_sql("EXPLAIN QUERY PLAN SELECT * FROM users")

    assert adapter.explained_sql == "SELECT * FROM users"


def test_explain_sql_strips_explain_analyze_prefix():
    adapter = ExplainSpyAdapter(ConnectionConfig(name="local", type="sqlite", path="/tmp/test.db"))
    query = QueryTools(adapter, DisclosureContext())

    query.explain_sql("EXPLAIN ANALYZE SELECT * FROM users")

    assert adapter.explained_sql == "SELECT * FROM users"


def test_explain_sql_strips_parenthesized_analyze_prefix():
    """EXPLAIN (ANALYZE, ...) executes the query; the prefix must be stripped so the
    re-prepended plain EXPLAIN does not run ANALYZE and bypass the cost gate."""
    for prefix in ("EXPLAIN (ANALYZE) ", "EXPLAIN (ANALYZE, BUFFERS) ", "EXPLAIN (FORMAT JSON) "):
        adapter = ExplainSpyAdapter(ConnectionConfig(name="local", type="sqlite", path="/tmp/test.db"))
        query = QueryTools(adapter, DisclosureContext())
        query.explain_sql(prefix + "SELECT * FROM users")
        assert adapter.explained_sql == "SELECT * FROM users"


def test_quote_identifier_handles_qualified_names():
    assert quote_identifier("public.orders", "postgres") == '"public"."orders"'
    assert quote_identifier("shop.orders", "mysql") == "`shop`.`orders`"
    # MariaDB uses backticks like MySQL — not double quotes (those are string literals).
    assert quote_identifier("shop.orders", "mariadb") == "`shop`.`orders`"
    assert quote_identifier("orders", "mariadb") == "`orders`"
    # Embedded backtick is doubled to escape it.
    assert quote_identifier("we`ird", "mariadb") == "`we``ird`"


def test_explain_sql_honors_table_scope():
    """There is no disclosure gate, so EXPLAIN runs for any table by default; but a
    configured connection table-scope (deny) is still enforced on EXPLAIN, so it
    can't be used to probe a denied table."""
    import pytest
    # Default (no scope): EXPLAIN any table.
    adapter = ExplainSpyAdapter(ConnectionConfig(name="local", type="sqlite", path="/tmp/test.db"))
    QueryTools(adapter, DisclosureContext()).explain_sql("SELECT * FROM anything")
    assert "anything" in adapter.explained_sql

    # With a deny scope: the denied table is rejected even via EXPLAIN.
    scoped = ExplainSpyAdapter(ConnectionConfig(
        name="local", type="sqlite", path="/tmp/test.db", table_deny=["secret_table"]))
    query = QueryTools(scoped, DisclosureContext())
    query.explain_sql("SELECT * FROM orders")  # in scope → ok
    with pytest.raises(ValueError, match="scope"):
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
    ctx.record_tables([TableInfo(name="users")], database="main")
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


def test_profile_and_schema_tools_enforce_table_scope(tmp_path):
    """table_deny/table_allow must be enforced on direct table access (sample_rows,
    column_stats, profile_table, describe_table, foreign_keys), not only execute_sql —
    otherwise the scope is bypassable via those tools."""
    import sqlite3
    import pytest
    from dbaide.adapters import build_adapter
    from dbaide.tools.profile import ProfileTools
    from dbaide.tools.schema import SchemaTools

    db = tmp_path / "scope.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE orders(id INTEGER)")
    c.execute("CREATE TABLE secret(id INTEGER, ssn TEXT)")
    c.execute("INSERT INTO secret VALUES (1, 'x')")
    c.commit(); c.close()

    conn = ConnectionConfig(name="local", type="sqlite", path=str(db), table_deny=["secret"])
    adapter = build_adapter(conn)
    pt = ProfileTools(adapter, DisclosureContext())
    st = SchemaTools(adapter, DisclosureContext())

    for fn in (lambda: pt.sample_rows("secret"),
               lambda: pt.column_stats("secret"),
               lambda: pt.profile_table("secret"),
               lambda: st.describe_table("secret"),
               lambda: st.foreign_keys("secret")):
        with pytest.raises(PermissionError, match="scope"):
            fn()

    # In-scope table still works.
    assert pt.sample_rows("orders").row_count == 0
    assert len(st.describe_table("orders")) == 1


def test_list_tables_filters_by_scope(tmp_path):
    """Enumeration must honor the connection scope: a denied table is hidden, and an
    allow-list shows only allowed tables (no info disclosure / no out-of-scope steering)."""
    import sqlite3
    from dbaide.adapters import build_adapter
    from dbaide.tools.schema import SchemaTools

    db = tmp_path / "scope.db"
    c = sqlite3.connect(db)
    for t in ("orders", "customers", "secret"):
        c.execute(f"CREATE TABLE {t}(id INTEGER)")
    c.commit(); c.close()

    deny = SchemaTools(build_adapter(ConnectionConfig(
        name="local", type="sqlite", path=str(db), table_deny=["secret"])), DisclosureContext())
    assert sorted(t.name for t in deny.list_tables()) == ["customers", "orders"]

    allow = SchemaTools(build_adapter(ConnectionConfig(
        name="local", type="sqlite", path=str(db), table_allow=["orders"])), DisclosureContext())
    assert [t.name for t in allow.list_tables()] == ["orders"]

    none = SchemaTools(build_adapter(ConnectionConfig(
        name="local", type="sqlite", path=str(db))), DisclosureContext())
    assert sorted(t.name for t in none.list_tables()) == ["customers", "orders", "secret"]
