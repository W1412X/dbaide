import types

from dbaide.agent.sql_writer import SQLWriter
from dbaide.adapters.mysql import MySQLAdapter
from dbaide.adapters.postgres import PostgresAdapter
from dbaide.config import ConfigManager
from dbaide.models import ColumnInfo, ConnectionConfig


def test_connection_session_timezone_defaults_and_roundtrips(tmp_path):
    conn = ConnectionConfig(name="shop", type="mysql")
    assert conn.session_timezone == "UTC"

    cfg = ConfigManager(path=tmp_path / "config.toml")
    cfg.upsert_connection(
        ConnectionConfig(name="shop", type="mysql", session_timezone="+08:00"),
        make_default=True,
    )

    reloaded = ConfigManager(path=tmp_path / "config.toml")
    assert reloaded.connections()["shop"].session_timezone == "+08:00"


class _PromptCaptureLLM:
    def __init__(self):
        self.last_system = ""
        self.last_user = ""

    def complete_json(self, messages, schema_hint=""):
        self.last_system = messages[0].content
        self.last_user = messages[1].content
        return {"sql": "SELECT 1", "rationale": "ok", "confidence": 0.9}


def test_sql_writer_prompt_includes_connection_session_timezone():
    llm = _PromptCaptureLLM()
    writer = SQLWriter(llm, dialect="mysql", server_version="8.0.36", session_timezone="+08:00")
    writer.write("today's orders", "orders", [ColumnInfo(name="created_at", data_type="timestamp")], context={})

    assert "Connection session time zone: +08:00" in llm.last_system
    assert "Connection session time zone: +08:00" in llm.last_user
    assert "do not assume it is the business timezone" in llm.last_system


class _MySQLCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.conn.executed.append((sql, params))


class _MySQLConnection:
    open = True

    def __init__(self):
        self.executed = []
        self.commits = 0

    def cursor(self):
        return _MySQLCursor(self)

    def commit(self):
        self.commits += 1


def test_mysql_open_connection_sets_session_timezone(monkeypatch):
    made = []

    def connect(**_kwargs):
        conn = _MySQLConnection()
        made.append(conn)
        return conn

    monkeypatch.setitem(
        __import__("sys").modules,
        "pymysql",
        types.SimpleNamespace(connect=connect, cursors=types.SimpleNamespace(DictCursor=object)),
    )

    adapter = MySQLAdapter(ConnectionConfig(name="shop", type="mysql", session_timezone="UTC"))
    adapter._open_connection("shop")

    assert made[0].executed == [("SET time_zone = %s", ("+00:00",))]
    assert made[0].commits == 1


class _PostgresConnection:
    def __init__(self):
        self.executed = []
        self.commits = 0

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def commit(self):
        self.commits += 1


def test_postgres_open_connection_sets_session_timezone(monkeypatch):
    made = []

    def connect(**_kwargs):
        conn = _PostgresConnection()
        made.append(conn)
        return conn

    modules = __import__("sys").modules
    monkeypatch.setitem(modules, "psycopg", types.SimpleNamespace(connect=connect))
    monkeypatch.setitem(modules, "psycopg.rows", types.SimpleNamespace(dict_row=object()))

    adapter = PostgresAdapter(ConnectionConfig(name="warehouse", type="postgres", session_timezone="UTC"))
    adapter._open_connection("warehouse")

    assert made[0].executed == [("SELECT set_config('TimeZone', %s, false)", ("UTC",))]
    assert made[0].commits == 1
