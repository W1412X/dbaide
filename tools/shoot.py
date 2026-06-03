"""Offscreen screenshot harness for the DBAide desktop UI.

Builds the real MainWindow against a temp sqlite db with realistic data, drives a
populated conversation (question → trace → answer → result table), and grabs the
full window plus individual regions to /tmp/shots/. Re-run after each UI edit to
compare. Usage: QT_QPA_PLATFORM=offscreen .venv/bin/python tools/shoot.py [tag]
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication, QWidget

from dbaide.assets import AssetStore
from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.desktop.theme import APP_STYLE
from dbaide.desktop.views.main_window import MainWindow
from dbaide.models import ConnectionConfig, ModelConfig

TAG = sys.argv[1] if len(sys.argv) > 1 else "base"
OUT = Path("/tmp/shots")
OUT.mkdir(exist_ok=True)


def _seed_db(path: Path) -> None:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, email TEXT, city TEXT, created_at TEXT);
        CREATE TABLE orders(id INTEGER PRIMARY KEY, user_id INT REFERENCES users(id),
                            amount REAL, status TEXT, created_at TEXT);
        CREATE TABLE products(id INTEGER PRIMARY KEY, sku TEXT, name TEXT, price REAL, category TEXT);
        CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INT REFERENCES orders(id),
                                 product_id INT REFERENCES products(id), qty INT);
        CREATE TABLE shipments(id INTEGER PRIMARY KEY, order_id INT REFERENCES orders(id),
                               carrier TEXT, tracking TEXT, shipped_at TEXT);
        """
    )
    for i in range(1, 40):
        c.execute("INSERT INTO users VALUES (?,?,?,?,?)",
                  (i, f"User {i}", f"user{i}@example.com", ["NYC", "SF", "LA", "Tokyo"][i % 4], "2024-01-01"))
        c.execute("INSERT INTO orders VALUES (?,?,?,?,?)",
                  (i, i, round(9.9 * i, 2), ["paid", "pending", "refunded"][i % 3], "2024-02-01"))
    c.commit()
    c.close()


def build_window(app: QApplication) -> MainWindow:
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "shop.db"
    _seed_db(db)
    cfg = ConfigManager(path=tmp / "config.toml")
    cfg.upsert_connection(ConnectionConfig(name="shop", type="sqlite", path=str(db)), make_default=True)
    cfg.upsert_model(
        ModelConfig(name="gpt-4o", provider="openai", base_url="https://api.openai.com/v1",
                    api_key="sk-test", model="gpt-4o"),
        make_default=True,
    )
    store = AssetStore(tmp / "assets")
    # Build assets (no profiling / no LLM) so the sidebar schema tree populates.
    from dbaide.adapters import build_adapter
    from dbaide.assets import AssetBuilder
    from dbaide.joins import JoinCatalogStore
    conn = cfg.connections()["shop"] if hasattr(cfg, "connections") else ConnectionConfig(name="shop", type="sqlite", path=str(db))
    jc = JoinCatalogStore(base_dir=tmp / "joins")
    AssetBuilder(connection=conn, adapter=build_adapter(conn), store=store, join_catalog=jc).build(profile_mode="none", sample=False)
    service = DesktopService(cfg, store)
    # Seed a few chat sessions so the Chats sidebar renders populated.
    from dbaide.history.session_store import ChatSessionStore, make_turn
    service.sessions = ChatSessionStore(base_dir=tmp / "sessions")
    for q in ["Top cities by paid order value", "Refund rate last quarter", "Products never ordered"]:
        s = service.sessions.create("shop")
        service.sessions.append_turn("shop", s["session_id"], make_turn(question=q, answer_markdown="…"))
    win = MainWindow(service)
    win.resize(1480, 920)
    win.show()
    QThreadPool.globalInstance().waitForDone(4000)
    app.processEvents()
    return win


def populate(win: MainWindow) -> None:
    """Drive a realistic populated conversation + trace + result table."""
    ask = win.ask_tab
    ask.set_has_connection(True)
    key = "demo"
    win._active_key = key
    ask.set_active(key)
    ask.begin_turn(key, "Which cities have the most paying users, and what's their total order value?",
                   connection="shop", database="auto", policy="safe_auto")
    events = [
        {"stage": "loop", "title": "started", "status": "completed", "kind": "agent"},
        {"stage": "decompose", "title": "1 intent · data_query", "status": "completed", "kind": "phase", "step": 0},
        {"stage": "resolve_schema", "title": "resolve_schema", "status": "completed", "kind": "tool",
         "step": 1, "detail": "users(id, city), orders(user_id, amount, status) · 1 join",
         "duration_ms": 820},
        {"stage": "resolve_schema", "title": "Schema discovery (round 1)", "status": "completed",
         "kind": "subagent", "parent": "resolve_schema", "node_id": "rs/d1", "detail": "2 candidate tables"},
        {"stage": "resolve_schema", "title": "confirmed orders: 3 col(s)", "status": "completed",
         "kind": "subagent", "parent": "resolve_schema", "node_id": "rs/c1"},
        {"stage": "resolve_schema", "title": "Map relations", "status": "completed",
         "kind": "subagent", "parent": "resolve_schema", "node_id": "rs/rel", "detail": "1 join"},
        {"stage": "generate_sql", "title": "generate_sql", "status": "completed", "kind": "tool",
         "step": 2, "detail": "GROUP BY city, sum amount", "duration_ms": 1430},
        {"stage": "execute_sql", "title": "execute_sql", "status": "completed", "kind": "tool", "step": 3,
         "sql": "SELECT u.city, COUNT(*) AS users, SUM(o.amount) AS total\nFROM users u\nJOIN orders o ON o.user_id = u.id\nWHERE o.status = 'paid'\nGROUP BY u.city\nORDER BY total DESC",
         "row_count": 4, "duration_ms": 36},
        {"stage": "loop", "title": "done", "status": "completed", "kind": "agent"},
    ]
    answer = (
        "Across paid orders, **Tokyo** leads with the highest total order value, followed by NYC.\n\n"
        "| City | Paying users | Total |\n|------|------|------|\n"
        "| Tokyo | 10 | $1,234.50 |\n| NYC | 10 | $1,100.00 |\n| SF | 9 | $980.10 |\n| LA | 9 | $870.30 |\n\n"
        "The values come from joining `orders` to `users` on `user_id` and filtering to `status = 'paid'`."
    )
    ask.append_result(key, {
        "status": "completed", "answer_markdown": answer,
        "selected_sql": "SELECT u.city, COUNT(*) AS users, SUM(o.amount) AS total\nFROM users u JOIN orders o ON o.user_id = u.id\nWHERE o.status = 'paid' GROUP BY u.city ORDER BY total DESC",
        "trace": events, "workflow_id": "wf_8a21",
    })
    win.right.show_trace(events)
    win.right.focus_trace()
    app = QApplication.instance()
    app.processEvents()
    # Select a rich node so the detail pane renders populated (execute_sql → SQL).
    tree = win.right.trace._tree
    for i in range(tree.topLevelItemCount()):
        it = tree.topLevelItem(i)
        data = it.data(0, 0x0100)  # Qt.ItemDataRole.UserRole
        if isinstance(data, dict) and data.get("node_type") == "sql":
            win.right.trace._on_click(it, 1)
            break
    app.processEvents()
    # SQL tab populated too.
    win.sql_tab.set_sql("SELECT u.city, COUNT(*) AS users, SUM(o.amount) AS total\nFROM users u\nJOIN orders o ON o.user_id = u.id\nWHERE o.status = 'paid'\nGROUP BY u.city\nORDER BY total DESC;")
    win.sql_tab.show_result({
        "columns": ["city", "users", "total"],
        "rows": [{"city": "Tokyo", "users": 10, "total": 1234.5},
                 {"city": "NYC", "users": 10, "total": 1100.0},
                 {"city": "SF", "users": 9, "total": 980.1},
                 {"city": "LA", "users": 9, "total": 870.3}],
        "row_count": 4, "elapsed_ms": 36,
    })
    app.processEvents()


def grab(widget: QWidget, name: str) -> None:
    app = QApplication.instance()
    for _ in range(3):
        app.processEvents()
    widget.grab().save(str(OUT / f"{TAG}__{name}.png"))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(APP_STYLE)
    win = build_window(app)
    populate(win)
    for _ in range(5):
        app.processEvents()
    grab(win, "window_ask")
    grab(win.topbar, "topbar")
    grab(win.sidebar, "sidebar")
    grab(win.composer, "composer")
    grab(win.right, "right_panel")
    grab(win.ask_tab, "ask_tab")
    # Workbench (SQL editor)
    win.switch_tab("SQL")
    for _ in range(4):
        app.processEvents()
    grab(win, "window_sql")
    grab(win.sql_tab, "sql_tab")
    print(f"shots → {OUT} (tag={TAG})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
