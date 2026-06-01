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

    table_events = [e for e in events if isinstance(e, dict)
                    and str(e.get("node_id", "")).startswith("build:table:")]
    assert table_events, "expected a per-table build node"
    orders = next(e for e in table_events if e["node_id"].endswith("orders"))
    assert orders["kind"] == "sql"
    assert orders["parent_id"].startswith("build:db:")
    assert "SELECT" in (orders.get("sql") or "").upper()

    # The trace model classifies it as a SQL step nested under the build tree.
    model = TraceModel()
    for e in events:
        if isinstance(e, dict):
            model.ingest(e)
    model.finalize()
    node = model.find(orders["node_id"])
    assert node is not None and node.node_type == "sql"
    assert "SELECT" in (node.raw.get("sql") or "").upper()
