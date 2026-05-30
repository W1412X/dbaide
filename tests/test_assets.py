import sqlite3

from dbaide.adapters import build_adapter
from dbaide.adapters.base import DatabaseAdapter, rows_to_result
from dbaide.assets import AssetBuilder, AssetStore
from dbaide.assets.summarizer import ASSET_SCHEMA_VERSION
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

    id_doc = store.read_json(store.column_dir("local", "main", "orders") / "id.json")
    total_doc = store.read_json(store.column_dir("local", "main", "orders") / "total_amount.json")
    status_doc = store.read_json(store.column_dir("local", "main", "orders") / "status.json")
    note_doc = store.read_json(store.column_dir("local", "main", "orders") / "note.json")
    table_doc = store.read_json(store.table_dir("local", "main", "orders") / "table.json")
    instance_doc = store.read_json(store.instance_dir("local") / "instance.json")

    assert id_doc["asset_schema_version"] == ASSET_SCHEMA_VERSION
    assert id_doc["profile_status"] == "profiled"
    assert id_doc["primary_key"] is True
    assert "likely_role" not in id_doc
    assert "semantic_tags" not in id_doc

    assert total_doc["profile_status"] == "profiled"
    assert total_doc["statistics"]["data_kind"] == "categorical"
    assert total_doc["statistics"]["distinct_count"] == 2

    assert status_doc["profile_status"] == "not_profiled"
    assert note_doc["profile_status"] == "not_profiled"
    assert "likely_role" not in note_doc

    assert "role_index" not in table_doc
    assert "join_hints" not in table_doc
    assert "id" in table_doc["column_index"]["primary_key"]
    assert instance_doc["asset_schema_version"] == ASSET_SCHEMA_VERSION


def test_table_doc_stores_only_declared_foreign_keys():
    from dbaide.assets.summarizer import AssetSummarizer
    from dbaide.models import ForeignKeyInfo

    summarizer = AssetSummarizer()
    table = TableInfo(name="orders")
    columns = [
        {
            "name": "user_id",
            "table": "orders",
            "data_type": "INTEGER",
            "primary_key": False,
            "indexed": False,
            "source_comment": "",
            "semantic_summary": "user_id: INTEGER",
        }
    ]
    doc = summarizer.table_doc(
        instance="local",
        database="main",
        table=table,
        columns=columns,
        foreign_keys=[],
    )
    assert doc["foreign_keys"] == []
    assert "join_hints" not in doc

    doc_with_fk = summarizer.table_doc(
        instance="local",
        database="main",
        table=table,
        columns=columns,
        foreign_keys=[ForeignKeyInfo("orders", "user_id", "users", "id")],
    )
    assert len(doc_with_fk["foreign_keys"]) == 1
    assert doc_with_fk["foreign_keys"][0]["ref_table"] == "users"
    assert doc_with_fk["foreign_keys"][0]["source"] == "foreign_key"


def test_asset_builder_foreign_keys_from_adapter(tmp_path):
    db = tmp_path / "fk.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            total_amount REAL
        );
        """
    )
    conn.commit()
    conn.close()

    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    store = AssetStore(tmp_path / "assets")
    AssetBuilder(connection=cfg, adapter=adapter, store=store).build(profile_mode="none", sample=False)

    table_doc = store.read_json(store.table_dir("local", "main", "orders") / "table.json")
    assert "join_hints" not in table_doc
    assert len(table_doc["foreign_keys"]) == 1
    assert table_doc["foreign_keys"][0]["column"] == "user_id"
    assert table_doc["foreign_keys"][0]["ref_table"] == "users"

    user_id_doc = store.read_json(store.column_dir("local", "main", "orders") / "user_id.json")
    assert "semantic_tags" not in user_id_doc


def test_asset_builder_discovers_all_databases(tmp_path):
    conn = ConnectionConfig(name="analysis", type="mysql", database="productdata")
    adapter = FakeAdapter(conn)
    store = AssetStore(tmp_path / "assets")

    stats = AssetBuilder(connection=conn, adapter=adapter, store=store).build(profile_mode="none", sample=False)

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
