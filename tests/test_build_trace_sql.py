"""The build phase emits a typed SQL node per table carrying the queries it ran,
so build SQL is auditable in the trace (no separate SQL Log needed)."""

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.assets import AssetBuilder, AssetStore
from dbaide.agent.trace_model import TraceModel
from dbaide.models import ConnectionConfig


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL, status TEXT);
        INSERT INTO orders VALUES (1, 10.5, 'paid'), (2, 20.0, 'pending');
        """
    )
    conn.commit()
    conn.close()


def test_build_emits_per_table_sql_nodes(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(conn)
    store = AssetStore(tmp_path / "assets")
    events: list = []
    AssetBuilder(connection=conn, adapter=adapter, store=store,
                 progress=events.append).build(profile_mode="auto")

    orders_events = [e for e in events if isinstance(e, dict)
                     and str(e.get("node_id", "")) == "build:table:main.orders"]
    assert orders_events, "expected a per-table build node"
    # Real-time: the table node is emitted 'running' (before completion) and updated
    # with a per-column note, not only once at the end.
    assert orders_events[0]["status"] == "running"
    assert any("·" in str(e.get("title", "")) and e["status"] == "running" for e in orders_events)
    assert orders_events[-1]["status"] == "completed"
    # The final node carries the queries it ran.
    final = orders_events[-1]
    assert final["kind"] == "sql"
    assert "SELECT" in (final.get("sql") or "").upper()

    # The trace model folds the stream into one SQL node nested under the build tree.
    model = TraceModel()
    for e in events:
        if isinstance(e, dict):
            model.ingest(e)
    model.finalize()
    node = model.find("build:table:main.orders")
    assert node is not None and node.node_type == "sql"
    assert "SELECT" in (node.raw.get("sql") or "").upper()
