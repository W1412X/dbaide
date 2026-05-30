from dbaide.adapters.base import DatabaseAdapter, quote_identifier, rows_to_result
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ColumnInfo, ColumnProfile, ConnectionConfig, TableInfo
from dbaide.tools import QueryTools


def test_explain_sql_does_not_double_prefix_explicit_explain():
    adapter = ExplainSpyAdapter(ConnectionConfig(name="local", type="sqlite", path="/tmp/test.db"))
    query = QueryTools(adapter, DisclosureContext())

    query.explain_sql("EXPLAIN SELECT * FROM users")

    assert adapter.explained_sql == "SELECT * FROM users"


def test_quote_identifier_handles_qualified_names():
    assert quote_identifier("public.orders", "postgres") == '"public"."orders"'
    assert quote_identifier("shop.orders", "mysql") == "`shop`.`orders`"


class ExplainSpyAdapter(DatabaseAdapter):
    dialect = "sqlite"

    def __init__(self, config):
        super().__init__(config)
        self.explained_sql = ""

    def test(self) -> None:
        return None

    def list_databases(self) -> list[str]:
        return ["main"]

    def list_tables(self, database: str = "") -> list[TableInfo]:
        return []

    def describe_table(self, table: str, database: str = "") -> list[ColumnInfo]:
        return []

    def execute_readonly(self, sql: str, *, database: str = "", limit: int | None = None, timeout_seconds: int = 10):
        return rows_to_result([], sql=sql)

    def explain(self, sql: str, *, database: str = "", timeout_seconds: int = 10):
        self.explained_sql = sql
        return rows_to_result([], sql="EXPLAIN " + sql)

    def sample_rows(self, table: str, *, database: str = "", limit: int = 20):
        return rows_to_result([], sql="")

    def profile_column(self, table: str, column: str, *, database: str = "", top_k: int = 10,
                       timeout_seconds: int = 30) -> ColumnProfile:
        return ColumnProfile(table=table, column=column, row_count=0, null_count=0)
