from dbaide.adapters.mysql import MySQLAdapter
from dbaide.db.connection_pool import reset_registry
from dbaide.models import ConnectionConfig


class _Cursor:
    def __init__(self, adapter):
        self.adapter = adapter
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        if "information_schema.COLUMNS" in sql:
            self.adapter.calls["columns"] += 1
            self._rows = [
                {
                    "TABLE_NAME": "orders",
                    "COLUMN_NAME": "id",
                    "DATA_TYPE": "bigint",
                    "IS_NULLABLE": "NO",
                    "COLUMN_DEFAULT": None,
                    "COLUMN_COMMENT": "",
                    "COLUMN_KEY": "PRI",
                },
                {
                    "TABLE_NAME": "users",
                    "COLUMN_NAME": "id",
                    "DATA_TYPE": "bigint",
                    "IS_NULLABLE": "NO",
                    "COLUMN_DEFAULT": None,
                    "COLUMN_COMMENT": "",
                    "COLUMN_KEY": "PRI",
                },
            ]
        elif "information_schema.KEY_COLUMN_USAGE" in sql:
            self.adapter.calls["foreign_keys"] += 1
            self._rows = [
                {
                    "TABLE_NAME": "orders",
                    "COLUMN_NAME": "user_id",
                    "REFERENCED_TABLE_NAME": "users",
                    "REFERENCED_COLUMN_NAME": "id",
                }
            ]
        elif "information_schema.STATISTICS" in sql:
            self.adapter.calls["indexes"] += 1
            self._rows = [
                {
                    "TABLE_NAME": "orders",
                    "INDEX_NAME": "PRIMARY",
                    "NON_UNIQUE": 0,
                    "SEQ_IN_INDEX": 1,
                    "COLUMN_NAME": "id",
                    "INDEX_TYPE": "BTREE",
                },
                {
                    "TABLE_NAME": "users",
                    "INDEX_NAME": "PRIMARY",
                    "NON_UNIQUE": 0,
                    "SEQ_IN_INDEX": 1,
                    "COLUMN_NAME": "id",
                    "INDEX_TYPE": "BTREE",
                },
            ]
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)


class _Connection:
    def __init__(self, adapter):
        self.adapter = adapter

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _Cursor(self.adapter)


class _CachingMySQLAdapter(MySQLAdapter):
    def __init__(self):
        super().__init__(ConnectionConfig(name="shop", type="mysql", database="shop"))
        self.calls = {"columns": 0, "foreign_keys": 0, "indexes": 0}

    def _connect(self, database: str = ""):
        return _Connection(self)


def test_mysql_metadata_is_loaded_once_per_database():
    adapter = _CachingMySQLAdapter()

    assert adapter.describe_table("orders", database="shop")[0].name == "id"
    assert adapter.describe_table("users", database="shop")[0].name == "id"
    assert adapter.foreign_keys("orders", database="shop")[0].ref_table == "users"
    assert adapter.foreign_keys("users", database="shop") == []
    assert adapter.indexes("orders", database="shop")[0].primary is True
    assert adapter.indexes("users", database="shop")[0].primary is True

    assert adapter.calls == {"columns": 1, "foreign_keys": 1, "indexes": 1}


class _RawConnection:
    open = True

    def __init__(self, name: int) -> None:
        self.name = name
        self.closed = False

    def ping(self, reconnect=False):
        if self.closed:
            raise RuntimeError("closed")

    def close(self):
        self.closed = True

    def rollback(self):
        return None


class _PooledMySQLAdapter(MySQLAdapter):
    def __init__(self):
        super().__init__(ConnectionConfig(name="pooled", type="mysql", database="shop"))
        self.opened = []

    def _open_connection(self, database: str = ""):
        conn = _RawConnection(len(self.opened))
        self.opened.append(conn)
        return conn


def test_mysql_adapter_reuses_physical_connections_from_pool():
    reset_registry()
    adapter = _PooledMySQLAdapter()

    with adapter._connect("shop") as first:
        assert first.name == 0
    with adapter._connect("shop") as second:
        assert second.name == 0

    assert len(adapter.opened) == 1


def test_mysql_list_tables_normalizes_table_type():
    """information_schema TABLE_TYPE ('BASE TABLE'/'VIEW') must be normalized to the
    lowercase 'table'/'view' the app expects — otherwise backup_database (which filters
    table_type == 'table') skips every MySQL table."""
    reset_registry()

    class _TablesCursor(_Cursor):
        def execute(self, sql, params=()):
            if "information_schema.TABLES" in sql:
                self._rows = [
                    {"name": "orders", "comment": "", "estimated_rows": 5, "table_type": "BASE TABLE"},
                    {"name": "v_summary", "comment": "", "estimated_rows": None, "table_type": "VIEW"},
                ]
            else:
                super().execute(sql, params)

    class _Conn(_Connection):
        def cursor(self):
            return _TablesCursor(self.adapter)

    class _Adapter(_CachingMySQLAdapter):
        def _connect(self, database: str = ""):
            return _Conn(self)

    tables = _Adapter().list_tables(database="shop")
    by_name = {t.name: t.table_type for t in tables}
    assert by_name == {"orders": "table", "v_summary": "view"}
