import sqlite3

from dbaide.adapters.base import DatabaseAdapter, rows_to_result
from dbaide.adapters import build_adapter
from dbaide.assets import AssetBuilder, AssetStore
from dbaide.models import ColumnInfo, ColumnProfile, ConnectionConfig, TableInfo


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            total_amount REAL,
            status TEXT,
            created_at TEXT,
            note TEXT
        );
        INSERT INTO orders VALUES
            (1, 10.5, 'paid', DATE('now', '-1 day'), 'first order'),
            (2, 20.0, 'pending', DATE('now', '-2 day'), 'second order');
        """
    )
    conn.commit()
    conn.close()


def test_asset_builder_creates_hierarchy(tmp_path):
    db = tmp_path / "app.db"
    make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    store = AssetStore(tmp_path / "assets")
    stats = AssetBuilder(connection=conn, adapter=adapter, store=store).build()
    assert stats.databases == 1
    assert stats.tables == 1
    assert stats.columns == 5
    assert (store.instance_dir("local") / "instance.json").exists()
    assert (store.database_dir("local", "main") / "database.json").exists()
    assert (store.table_dir("local", "main", "orders") / "table.json").exists()
    status_doc = store.read_json(store.column_dir("local", "main", "orders") / "status.json")
    note_doc = store.read_json(store.column_dir("local", "main", "orders") / "note.json")
    table_doc = store.read_json(store.table_dir("local", "main", "orders") / "table.json")
    instance_doc = store.read_json(store.instance_dir("local") / "instance.json")
    assert status_doc["likely_role"] == "categorical_status"
    assert status_doc["profile"]["distinct_count"] == 2
    assert status_doc["statistics"]["data_kind"] == "categorical"
    assert status_doc["statistics"]["distribution"]["top_values_coverage"] == 1.0
    assert note_doc["profile_status"] == "not_profiled"
    assert note_doc["likely_role"] == "text"
    assert "categorical_status" in table_doc["role_index"]
    assert instance_doc["asset_schema_version"] == 2


def test_asset_builder_discovers_all_databases(tmp_path):
    conn = ConnectionConfig(name="analysis", type="mysql", database="productdata")
    adapter = FakeAdapter(conn)
    store = AssetStore(tmp_path / "assets")

    stats = AssetBuilder(connection=conn, adapter=adapter, store=store).build(profile_mode="none", sample=False)

    # Now always discovers all databases, not just the configured one
    assert adapter.list_databases_called is True
    assert stats.databases >= 1


class FakeAdapter(DatabaseAdapter):
    dialect = "mysql"

    def __init__(self, config):
        super().__init__(config)
        self.list_databases_called = False
        self.requested_databases = []

    def test(self) -> None:
        return None

    def list_databases(self) -> list[str]:
        self.list_databases_called = True
        return ["analysis"]

    def list_tables(self, database: str = "") -> list[TableInfo]:
        self.requested_databases.append(database)
        return []

    def describe_table(self, table: str, database: str = "") -> list[ColumnInfo]:
        return []

    def execute_readonly(self, sql: str, *, database: str = "", limit: int | None = None, timeout_seconds: int = 10):
        return rows_to_result([], sql=sql)

    def explain(self, sql: str, *, database: str = "", timeout_seconds: int = 10):
        return rows_to_result([], sql=sql)

    def sample_rows(self, table: str, *, database: str = "", limit: int = 20):
        return rows_to_result([], sql="")

    def profile_column(self, table: str, column: str, *, database: str = "", top_k: int = 10,
                       timeout_seconds: int = 30) -> ColumnProfile:
        return ColumnProfile(table=table, column=column, row_count=0, null_count=0)
