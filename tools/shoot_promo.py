"""Generate promotional screenshots for DBAide.

The script starts the real desktop UI, seeds a complex ecommerce
SQLite database, builds assets, drives representative assistant/workbench states,
and writes screenshots plus a copy deck to docs/images/promo/.

Usage:
    ./venv/bin/python tools/shoot_promo.py

The chart-answer scenarios are captured from the real Qt WebEngine answer view.
If WebEngine is unavailable or the page does not render, the script fails instead
of falling back to placeholder text or composited PNGs.
"""
from __future__ import annotations

import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-dev-shm-usage --disable-gpu",
)

from dbaide.desktop.platform_ui import ensure_webengine_before_qapplication

if not ensure_webengine_before_qapplication():
    raise RuntimeError("PyQt6-WebEngine is required for promo screenshots.")

from PyQt6.QtCore import QEventLoop, Qt
from PyQt6.QtWidgets import QApplication

from dbaide.assets import AssetBuilder, AssetStore
from dbaide.adapters import build_adapter
from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.desktop.dialogs.backup import BackupManager
from dbaide.desktop.dialogs.build_assets import BuildAssetsDialog
from dbaide.desktop.dialogs.connection import ConnectionDialog
from dbaide.desktop.dialogs.settings import SettingsDialog
from dbaide.desktop.theme import Theme, app_style
from dbaide.desktop.views.main_window import MainWindow
from dbaide.i18n import set_language
from dbaide.joins import JoinCatalogStore
from dbaide.models import ConnectionConfig, ModelConfig


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images" / "promo"
TMP = Path(tempfile.mkdtemp(prefix="dbaide-promo-"))

DOC_SCENARIOS = [
    "assets",
    "thinking",
    "trace",
    "analysis",
    "breakdown",
    "clarify",
    "sql",
    "table",
    "field",
    "dep-tree",
    "audit",
    "settings-connections",
    "settings-models",
    "settings-resources",
    "settings-integrations",
    "backup",
    "build-dialog",
    "connection-dialog",
]


def _docs_python_executable() -> str:
    venv_python = ROOT / "venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _verify_webengine_runtime() -> None:
    env = dict(os.environ)
    result = subprocess.run(
        [_docs_python_executable(), str(ROOT / "tools" / "probe_webengine_runtime.py")],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        env["DBAIDE_WEBENGINE_RUNTIME_VERIFIED"] = "1"
        os.environ["DBAIDE_WEBENGINE_RUNTIME_VERIFIED"] = "1"
        return
    detail = ""
    if os.environ.get("DBAIDE_DEBUG_WEBENGINE_PROBE") == "1":
        raw = (result.stderr or result.stdout or "").strip()
        if raw:
            detail = f"\n{raw}"
    raise SystemExit(
        "Qt WebEngine runtime probe failed. Promo screenshots must be generated from a "
        f"GUI-capable desktop session.{detail}"
    )


TABLES = [
    "users", "user_profiles", "addresses", "merchants", "brands",
    "categories", "products", "warehouses", "inventory_snapshots",
    "orders", "order_items", "payments", "refunds", "shipments",
    "shipment_events", "coupons", "coupon_redemptions", "reviews",
    "support_tickets", "ledger_entries", "risk_events", "experiments",
    "experiment_assignments", "ad_spend_daily",
]


def _exec_many(cur: sqlite3.Cursor, sql: str, rows: list[tuple[Any, ...]]) -> None:
    cur.executemany(sql, rows)


def seed_ecommerce_db(path: Path) -> None:
    rng = random.Random(20260612)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE users (
          id INTEGER PRIMARY KEY,
          email TEXT NOT NULL UNIQUE,
          first_seen_at TEXT NOT NULL,
          city TEXT NOT NULL,
          acquisition_channel TEXT NOT NULL,
          vip_tier TEXT NOT NULL
        );
        CREATE TABLE user_profiles (
          user_id INTEGER PRIMARY KEY REFERENCES users(id),
          birth_year INTEGER,
          gender TEXT,
          lifecycle_segment TEXT
        );
        CREATE TABLE addresses (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          province TEXT NOT NULL,
          city TEXT NOT NULL,
          is_default INTEGER NOT NULL
        );
        CREATE TABLE merchants (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          region TEXT NOT NULL,
          settlement_cycle TEXT NOT NULL
        );
        CREATE TABLE brands (
          id INTEGER PRIMARY KEY,
          merchant_id INTEGER NOT NULL REFERENCES merchants(id),
          name TEXT NOT NULL
        );
        CREATE TABLE categories (
          id INTEGER PRIMARY KEY,
          parent_id INTEGER REFERENCES categories(id),
          name TEXT NOT NULL,
          margin_band TEXT NOT NULL
        );
        CREATE TABLE products (
          id INTEGER PRIMARY KEY,
          brand_id INTEGER NOT NULL REFERENCES brands(id),
          category_id INTEGER NOT NULL REFERENCES categories(id),
          sku TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          list_price REAL NOT NULL,
          cost REAL NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE warehouses (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          city TEXT NOT NULL,
          is_crossdock INTEGER NOT NULL
        );
        CREATE TABLE inventory_snapshots (
          id INTEGER PRIMARY KEY,
          product_id INTEGER NOT NULL REFERENCES products(id),
          warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
          snapshot_date TEXT NOT NULL,
          on_hand_qty INTEGER NOT NULL,
          reserved_qty INTEGER NOT NULL
        );
        CREATE TABLE orders (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          address_id INTEGER NOT NULL REFERENCES addresses(id),
          order_no TEXT NOT NULL UNIQUE,
          ordered_at TEXT NOT NULL,
          status TEXT NOT NULL,
          channel TEXT NOT NULL,
          gross_amount REAL NOT NULL,
          discount_amount REAL NOT NULL,
          shipping_fee REAL NOT NULL
        );
        CREATE TABLE order_items (
          id INTEGER PRIMARY KEY,
          order_id INTEGER NOT NULL REFERENCES orders(id),
          product_id INTEGER NOT NULL REFERENCES products(id),
          quantity INTEGER NOT NULL,
          unit_price REAL NOT NULL,
          item_discount REAL NOT NULL
        );
        CREATE TABLE payments (
          id INTEGER PRIMARY KEY,
          order_id INTEGER NOT NULL REFERENCES orders(id),
          paid_at TEXT,
          provider TEXT NOT NULL,
          amount REAL NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE refunds (
          id INTEGER PRIMARY KEY,
          order_id INTEGER NOT NULL REFERENCES orders(id),
          item_id INTEGER REFERENCES order_items(id),
          requested_at TEXT NOT NULL,
          reason TEXT NOT NULL,
          amount REAL NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE shipments (
          id INTEGER PRIMARY KEY,
          order_id INTEGER NOT NULL REFERENCES orders(id),
          warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
          carrier TEXT NOT NULL,
          shipped_at TEXT,
          delivered_at TEXT,
          status TEXT NOT NULL
        );
        CREATE TABLE shipment_events (
          id INTEGER PRIMARY KEY,
          shipment_id INTEGER NOT NULL REFERENCES shipments(id),
          event_at TEXT NOT NULL,
          event_type TEXT NOT NULL,
          city TEXT
        );
        CREATE TABLE coupons (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          campaign TEXT NOT NULL,
          discount_type TEXT NOT NULL,
          face_value REAL NOT NULL
        );
        CREATE TABLE coupon_redemptions (
          id INTEGER PRIMARY KEY,
          coupon_id INTEGER NOT NULL REFERENCES coupons(id),
          order_id INTEGER NOT NULL REFERENCES orders(id),
          user_id INTEGER NOT NULL REFERENCES users(id),
          redeemed_at TEXT NOT NULL
        );
        CREATE TABLE reviews (
          id INTEGER PRIMARY KEY,
          order_item_id INTEGER NOT NULL REFERENCES order_items(id),
          user_id INTEGER NOT NULL REFERENCES users(id),
          rating INTEGER NOT NULL,
          sentiment TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE support_tickets (
          id INTEGER PRIMARY KEY,
          order_id INTEGER REFERENCES orders(id),
          user_id INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL,
          category TEXT NOT NULL,
          priority TEXT NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE ledger_entries (
          id INTEGER PRIMARY KEY,
          order_id INTEGER REFERENCES orders(id),
          payment_id INTEGER REFERENCES payments(id),
          refund_id INTEGER REFERENCES refunds(id),
          entry_at TEXT NOT NULL,
          entry_type TEXT NOT NULL,
          amount REAL NOT NULL
        );
        CREATE TABLE risk_events (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          order_id INTEGER REFERENCES orders(id),
          event_at TEXT NOT NULL,
          rule_name TEXT NOT NULL,
          severity TEXT NOT NULL,
          action TEXT NOT NULL
        );
        CREATE TABLE experiments (
          id INTEGER PRIMARY KEY,
          key TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          started_at TEXT NOT NULL,
          ended_at TEXT
        );
        CREATE TABLE experiment_assignments (
          id INTEGER PRIMARY KEY,
          experiment_id INTEGER NOT NULL REFERENCES experiments(id),
          user_id INTEGER NOT NULL REFERENCES users(id),
          variant TEXT NOT NULL,
          assigned_at TEXT NOT NULL
        );
        CREATE TABLE ad_spend_daily (
          id INTEGER PRIMARY KEY,
          spend_date TEXT NOT NULL,
          channel TEXT NOT NULL,
          campaign TEXT NOT NULL,
          spend REAL NOT NULL,
          impressions INTEGER NOT NULL,
          clicks INTEGER NOT NULL
        );
        CREATE INDEX idx_orders_user_time ON orders(user_id, ordered_at);
        CREATE INDEX idx_items_order_product ON order_items(order_id, product_id);
        CREATE INDEX idx_payments_order_status ON payments(order_id, status);
        CREATE INDEX idx_refunds_order_status ON refunds(order_id, status);
        CREATE INDEX idx_shipments_order_status ON shipments(order_id, status);
        CREATE INDEX idx_inventory_product_date ON inventory_snapshots(product_id, snapshot_date);
        """
    )

    cities = ["上海", "北京", "深圳", "杭州", "成都", "武汉", "广州", "南京"]
    channels = ["搜索广告", "内容种草", "自然流量", "直播", "私域", "联盟"]
    tiers = ["普通", "银卡", "金卡", "黑卡"]
    users = []
    profiles = []
    addresses = []
    for uid in range(1, 241):
        city = cities[uid % len(cities)]
        channel = channels[(uid * 3) % len(channels)]
        users.append((uid, f"user{uid:04d}@example.com", "2025-01-01", city, channel, tiers[uid % len(tiers)]))
        profiles.append((uid, rng.randint(1976, 2004), ["女", "男", "未知"][uid % 3], ["new", "active", "at_risk", "loyal"][uid % 4]))
        addresses.append((uid, uid, city, city, 1))
    _exec_many(cur, "INSERT INTO users VALUES (?,?,?,?,?,?)", users)
    _exec_many(cur, "INSERT INTO user_profiles VALUES (?,?,?,?)", profiles)
    _exec_many(cur, "INSERT INTO addresses VALUES (?,?,?,?,?)", addresses)

    merchants = [(i, f"品牌商 {i}", ["华东", "华北", "华南", "西南"][i % 4], ["D+1", "周结", "月结"][i % 3]) for i in range(1, 9)]
    brands = [(i, (i % 8) + 1, f"Brand-{i:02d}") for i in range(1, 17)]
    categories = [
        (1, None, "3C数码", "中"), (2, None, "家居生活", "高"), (3, None, "美妆个护", "高"),
        (4, None, "运动户外", "中"), (5, 1, "智能配件", "中"), (6, 2, "厨房", "高"),
        (7, 3, "护肤", "高"), (8, 4, "露营", "中"),
    ]
    products = []
    for pid in range(1, 73):
        price = rng.choice([49, 79, 129, 199, 299, 399, 599, 899]) + rng.randint(0, 30)
        cost = round(price * rng.uniform(0.42, 0.68), 2)
        products.append((pid, (pid % 16) + 1, (pid % 8) + 1, f"SKU-{pid:05d}", f"精选商品 {pid}", price, cost, "active"))
    warehouses = [(1, "华东一仓", "上海", 0), (2, "华北一仓", "北京", 0), (3, "华南前置仓", "深圳", 1), (4, "西南中心仓", "成都", 0)]
    _exec_many(cur, "INSERT INTO merchants VALUES (?,?,?,?)", merchants)
    _exec_many(cur, "INSERT INTO brands VALUES (?,?,?)", brands)
    _exec_many(cur, "INSERT INTO categories VALUES (?,?,?,?)", categories)
    _exec_many(cur, "INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", products)
    _exec_many(cur, "INSERT INTO warehouses VALUES (?,?,?,?)", warehouses)

    coupons = [
        (1, "SPRING30", "春季拉新", "amount", 30), (2, "VIP88", "会员复购", "amount", 88),
        (3, "LIVE15", "直播间转化", "percent", 15), (4, "FREESHIP", "包邮", "amount", 12),
    ]
    experiments = [(1, "checkout_reco_v2", "结算页推荐实验", "2026-03-01", None)]
    _exec_many(cur, "INSERT INTO coupons VALUES (?,?,?,?,?)", coupons)
    _exec_many(cur, "INSERT INTO experiments VALUES (?,?,?,?,?)", experiments)

    base = datetime(2026, 1, 1)
    order_id = item_id = payment_id = refund_id = shipment_id = 1
    orders = []
    items = []
    payments = []
    refunds = []
    shipments = []
    shipment_events = []
    coupon_redemptions = []
    reviews = []
    tickets = []
    ledger = []
    risk_events = []
    assignments = []
    ad_spend = []
    for day in range(0, 150):
        d = base + timedelta(days=day)
        for channel in channels:
            spend = rng.uniform(1200, 7800)
            ad_spend.append((len(ad_spend) + 1, d.strftime("%Y-%m-%d"), channel, f"{channel}-Q{1 + day // 90}", round(spend, 2), rng.randint(12000, 88000), rng.randint(300, 2600)))
        for _ in range(rng.randint(9, 19)):
            uid = rng.randint(1, 240)
            status = rng.choices(["paid", "delivered", "refunded", "cancelled"], weights=[30, 56, 8, 6])[0]
            channel = rng.choice(channels)
            line_count = rng.randint(1, 4)
            order_items = []
            gross = 0.0
            discount = 0.0
            for _line in range(line_count):
                product = products[rng.randint(0, len(products) - 1)]
                qty = rng.randint(1, 3)
                price = float(product[5])
                item_discount = round(price * qty * rng.choice([0, 0.05, 0.1, 0.15]), 2)
                gross += price * qty
                discount += item_discount
                order_items.append((item_id, order_id, product[0], qty, price, item_discount))
                item_id += 1
            shipping_fee = 0 if gross > 199 else 12
            orders.append((order_id, uid, uid, f"EC{d.strftime('%y%m%d')}{order_id:06d}", d.isoformat(timespec="seconds"), status, channel, round(gross, 2), round(discount, 2), shipping_fee))
            items.extend(order_items)
            if status != "cancelled":
                paid_at = (d + timedelta(minutes=rng.randint(2, 90))).isoformat(timespec="seconds")
                paid_amount = round(gross - discount + shipping_fee, 2)
                payments.append((payment_id, order_id, paid_at, rng.choice(["支付宝", "微信", "银行卡", "余额"]), paid_amount, "succeeded"))
                ledger.append((len(ledger) + 1, order_id, payment_id, None, paid_at, "payment", paid_amount))
                payment_id += 1
                wh = rng.randint(1, 4)
                shipped = d + timedelta(hours=rng.randint(6, 36))
                delivered = shipped + timedelta(hours=rng.randint(18, 96))
                shipments.append((shipment_id, order_id, wh, rng.choice(["顺丰", "京东物流", "中通", "圆通"]), shipped.isoformat(timespec="seconds"), delivered.isoformat(timespec="seconds") if status in ("delivered", "refunded") else None, "delivered" if status in ("delivered", "refunded") else "in_transit"))
                shipment_events.append((len(shipment_events) + 1, shipment_id, shipped.isoformat(timespec="seconds"), "picked", warehouses[wh - 1][2]))
                shipment_events.append((len(shipment_events) + 1, shipment_id, delivered.isoformat(timespec="seconds"), "delivered", users[uid - 1][3]))
                shipment_id += 1
            if rng.random() < 0.28:
                coupon = rng.randint(1, 4)
                coupon_redemptions.append((len(coupon_redemptions) + 1, coupon, order_id, uid, d.isoformat(timespec="seconds")))
            if status == "refunded":
                amount = round((gross - discount) * rng.uniform(0.35, 1.0), 2)
                refunds.append((refund_id, order_id, None, (d + timedelta(days=rng.randint(1, 10))).isoformat(timespec="seconds"), rng.choice(["尺码不合适", "质量问题", "未按时送达", "七天无理由"]), amount, "approved"))
                ledger.append((len(ledger) + 1, order_id, None, refund_id, d.isoformat(timespec="seconds"), "refund", -amount))
                refund_id += 1
            if rng.random() < 0.2:
                tickets.append((len(tickets) + 1, order_id, uid, (d + timedelta(hours=3)).isoformat(timespec="seconds"), rng.choice(["物流", "退款", "发票", "商品咨询"]), rng.choice(["P1", "P2", "P3"]), rng.choice(["open", "solved"])))
            if rng.random() < 0.06:
                risk_events.append((len(risk_events) + 1, uid, order_id, d.isoformat(timespec="seconds"), rng.choice(["同设备多账号", "高频退款", "支付异常"]), rng.choice(["low", "medium", "high"]), rng.choice(["allow", "review", "block"])))
            assignments.append((len(assignments) + 1, 1, uid, rng.choice(["A", "B"]), "2026-03-01"))
            order_id += 1

    for sid in range(1, 73):
        for wid in range(1, 5):
            qty = max(0, int(160 - sid * 1.3 + rng.randint(-20, 60)))
            reserved = rng.randint(0, min(qty, 50))
            cur.execute("INSERT INTO inventory_snapshots VALUES (?,?,?,?,?,?)", (len(TABLES) * 1000 + sid * 10 + wid, sid, wid, "2026-05-31", qty, reserved))

    _exec_many(cur, "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)", orders)
    _exec_many(cur, "INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
    _exec_many(cur, "INSERT INTO payments VALUES (?,?,?,?,?,?)", payments)
    _exec_many(cur, "INSERT INTO refunds VALUES (?,?,?,?,?,?,?)", refunds)
    _exec_many(cur, "INSERT INTO shipments VALUES (?,?,?,?,?,?,?)", shipments)
    _exec_many(cur, "INSERT INTO shipment_events VALUES (?,?,?,?,?)", shipment_events)
    _exec_many(cur, "INSERT INTO coupon_redemptions VALUES (?,?,?,?,?)", coupon_redemptions)
    _exec_many(cur, "INSERT INTO support_tickets VALUES (?,?,?,?,?,?,?)", tickets)
    _exec_many(cur, "INSERT INTO ledger_entries VALUES (?,?,?,?,?,?,?)", ledger)
    _exec_many(cur, "INSERT INTO risk_events VALUES (?,?,?,?,?,?,?)", risk_events)
    _exec_many(cur, "INSERT INTO experiment_assignments VALUES (?,?,?,?,?)", assignments)
    _exec_many(cur, "INSERT INTO ad_spend_daily VALUES (?,?,?,?,?,?,?)", ad_spend)
    conn.commit()
    conn.close()


def _build_window(app: QApplication) -> tuple[MainWindow, DesktopService]:
    case_dir = Path(tempfile.mkdtemp(prefix="case-", dir=TMP))
    db = case_dir / "omnichannel_ecommerce.db"
    seed_ecommerce_db(db)
    cfg = ConfigManager(path=case_dir / "config.toml")
    cfg.set_ui_language("zh")
    conn = ConnectionConfig(name="omni_shop", type="sqlite", path=str(db), load_profile="dev", session_timezone="+08:00")
    cfg.upsert_connection(conn, make_default=True)
    cfg.upsert_model(
        ModelConfig(
            name="OpenAI GPT-4.1",
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            api_key="sk-promo",
            model="gpt-4.1",
        ),
        make_default=True,
    )
    store = AssetStore(case_dir / "assets")
    AssetBuilder(
        connection=conn,
        adapter=build_adapter(conn),
        store=store,
        join_catalog=JoinCatalogStore(base_dir=case_dir / "joins"),
    ).build(profile_mode="none", sample=False)
    service = DesktopService(cfg, store)
    win = MainWindow(service)
    win.resize(1520, 960)
    win.show()
    _process(app, 8)
    rows = service.dispatch("schema_tree", {"name": "omni_shop"})
    win.schema_rows = rows
    win.sidebar.load_schema(rows)
    _process(app, 4)
    return win, service


def _process(app: QApplication, cycles: int = 3) -> None:
    for _ in range(cycles):
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 5)


def _wait_until(app: QApplication, predicate, *, timeout_s: float = 2.5) -> bool:
    deadline = time.monotonic() + max(0.1, timeout_s)
    while time.monotonic() < deadline:
        _process(app, 4)
        if predicate():
            return True
        time.sleep(0.03)
    _process(app, 4)
    return bool(predicate())


def _run_page_js_bool(page, app: QApplication, script: str, *, timeout_s: float = 1.5) -> bool:
    state = {"value": False, "done": False}

    def _apply(raw: object) -> None:
        state["value"] = bool(raw)
        state["done"] = True

    page.runJavaScript(script, _apply)
    deadline = time.monotonic() + max(0.05, timeout_s)
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        if state["done"]:
            return state["value"]
        time.sleep(0.02)
    return state["value"]


_CHART_READY_JS = """
(function(){
  if (!window.echarts) return false;
  const blocks = document.querySelectorAll('.chart-block').length;
  if (!blocks) return false;
  const canvases = document.querySelectorAll('.chart-canvas canvas').length;
  return canvases >= blocks;
})()
"""

_MARKDOWN_READY_JS = """
(function(){
  var root = document.querySelector('.answer-document') || document.body;
  if (!root) return false;
  var text = (root.innerText || '').replace(/\\s+/g, ' ').trim();
  return text.length > 40;
})()
"""


def _find_latest_turn(view) -> object | None:
    for i in range(view._layout.count() - 1, -1, -1):
        item = view._layout.itemAt(i)
        widget = item.widget() if item is not None else None
        if widget is not None and hasattr(widget, "_toggle_trace"):
            return widget
    return None


def _wait_for_trace_drawer(app: QApplication, win: MainWindow) -> bool:
    def _ready() -> bool:
        panel = getattr(win, "_trace_drawer_panel", None)
        return bool(panel is not None and panel.isVisible() and panel.width() > 120)

    return _wait_until(app, _ready, timeout_s=2.0)


def _answer_document_host(block) -> object | None:
    return getattr(block, "_rendered", None) or getattr(block, "_pending_rendered", None)


def _is_webengine_view(view) -> bool:
    if view is None:
        return False
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
    except Exception:
        return False
    return isinstance(view, QWebEngineView)


def _has_chart_webengine(block) -> bool:
    rendered = _answer_document_host(block)
    if rendered is None:
        return False
    view = getattr(rendered, "_view", None)
    return _is_webengine_view(view)


def _locate_answer_block(view) -> object | None:
    turn = _find_latest_turn(view)
    if turn is None:
        return None
    for i in range(turn._content.count() - 1, -1, -1):
        item = turn._content.itemAt(i)
        widget = item.widget() if item is not None else None
        if widget is not None and widget.__class__.__name__ == "AnswerDocumentBlock":
            return widget
    return None


def _ensure_chat_visible(win: MainWindow) -> None:
    win.switch_tab("Chat")


def _answer_page(app: QApplication, win: MainWindow, key: str):
    view = win.ask_tab.view(key)
    if view is None:
        raise RuntimeError(f"no conversation view for {key}")

    def _locate_block():
        return _locate_answer_block(view)

    if not _wait_until(
        app,
        lambda: (block := _locate_block()) is not None and _has_chart_webengine(block),
        timeout_s=8.0,
    ):
        block = _locate_block()
        if block is None:
            raise RuntimeError("answer document block not ready for screenshot")
        raise RuntimeError(
            "Answer screenshot requires PyQt6-WebEngine. "
            "Current environment fell back to plaintext answer rendering."
        )

    block = _locate_block()
    if block is None:
        raise RuntimeError("answer document block missing")
    if hasattr(block, "ensure_full_render"):
        block.ensure_full_render()
    _process(app, 24)

    if not _wait_until(
        app,
        lambda: (block := _locate_block()) is not None and getattr(block, "_rendered", None) is not None,
        timeout_s=8.0,
    ):
        raise RuntimeError("answer document WebEngine view did not commit before capture")

    block = _locate_block()
    rendered = _answer_document_host(block)
    page = getattr(getattr(rendered, "_view", None), "page", lambda: None)()
    if page is None:
        raise RuntimeError("answer page missing")
    return view, page


def _wait_for_answer_document(
    app: QApplication,
    win: MainWindow,
    key: str,
    *,
    require_charts: bool = False,
    chart_count: int = 0,
) -> None:
    view, page = _answer_page(app, win, key)

    if not _wait_until(
        app,
        lambda: _run_page_js_bool(page, app, _MARKDOWN_READY_JS, timeout_s=1.5),
        timeout_s=10.0,
    ):
        raise RuntimeError("answer markdown did not render before capture")

    if not require_charts:
        return

    chart_timeout = max(18.0, 6.0 + chart_count * 2.5)

    def _probe() -> bool:
        return _run_page_js_bool(page, app, _CHART_READY_JS, timeout_s=2.0)

    if not _wait_until(app, _probe, timeout_s=chart_timeout):
        raise RuntimeError("chart canvases did not render before capture")


def _wait_for_answer_charts(app: QApplication, win: MainWindow, key: str, *, chart_count: int = 0) -> None:
    _wait_for_answer_document(
        app,
        win,
        key,
        require_charts=True,
        chart_count=chart_count,
    )


def _grab(app: QApplication, widget, name: str) -> Path:
    _process(app, 6)
    path = OUT / f"{name}.png"
    widget.grab().save(str(path))
    return path


def _grab_trimmed(app: QApplication, widget, name: str, *, pad: int = 18) -> Path:
    """Grab a widget and trim the empty top/bottom background bands (keeping full width),
    so a drawer panel that is taller than its content has no large empty band."""
    _process(app, 6)
    path = OUT / f"{name}.png"
    widget.grab().save(str(path))
    from PIL import Image
    import numpy as np

    img = Image.open(path).convert("RGB")
    arr = np.asarray(img).astype("int16")
    # Background = the image's dominant colour (these dark screenshots are mostly empty
    # canvas), robust whether the empty band is at the top (conversation views) or the
    # bottom (a drawer panel taller than its content).
    bg = np.median(arr.reshape(-1, 3), axis=0)
    # A row counts as "content" if enough pixels differ meaningfully from the background.
    delta = np.abs(arr - bg).sum(axis=2)  # H x W
    row_content = (delta > 30).sum(axis=1)  # non-bg pixels per row
    rows = np.where(row_content > 4)[0]
    if rows.size:
        top = max(0, int(rows[0]) - pad)
        bottom = min(img.height, int(rows[-1]) + pad)
        img.crop((0, top, img.width, bottom)).save(path)
    return path


def _grab_scrolled(app: QApplication, view, widget, name: str, ratio: float) -> Path:
    _process(app, 6)
    bar = view.verticalScrollBar()
    bar.setValue(max(0, int(bar.maximum() * ratio)))
    app.processEvents()
    path = OUT / f"{name}.png"
    widget.grab().save(str(path))
    return path


def _expand_schema_tree(win: MainWindow, *, database_only: bool = False) -> None:
    tree = win.sidebar.tree
    for i in range(tree.topLevelItemCount()):
        db = tree.topLevelItem(i)
        if db is not None:
            db.setExpanded(True)
            if not database_only:
                for j in range(min(db.childCount(), 8)):
                    child = db.child(j)
                    if child is not None:
                        child.setExpanded(True)


def _partial_schema_rows(rows: list[dict[str, Any]], table_count: int = 9) -> list[dict[str, Any]]:
    out = []
    for db in rows:
        copy = dict(db)
        copy["children"] = list((db.get("children") or [])[:table_count])
        out.append(copy)
    return out


def _trace_events(final: bool = True) -> list[dict[str, Any]]:
    running = "completed" if final else "running"
    return [
        {"stage": "loop", "title": "启动智能体", "status": "completed", "kind": "phase", "step": 1,
         "thought": "先判断这是跨业务域分析，需要同时看订单、退款、履约、库存和投放。", "duration_ms": 210},
        {"stage": "discover_schema", "title": "发现候选表", "status": "completed", "kind": "tool", "step": 2,
         "detail": "命中 orders/order_items/payments/refunds/shipments/inventory_snapshots/ad_spend_daily 等 9 张表",
         "duration_ms": 620},
        {"stage": "retrieve_schema_context", "title": "读取结构证据", "status": "completed", "kind": "subagent",
         "parent": "discover_schema", "node_id": "schema/orders", "detail": "orders.status、gross_amount、discount_amount、channel、ordered_at"},
        {"stage": "retrieve_join_context", "title": "推断并校验关联", "status": "completed", "kind": "tool", "step": 3,
         "detail": "orders.id = order_items.order_id；orders.id = payments.order_id；orders.id = refunds.order_id；products.category_id = categories.id",
         "duration_ms": 780},
        {"stage": "generate_sql", "title": "生成多段 SQL", "status": "completed", "kind": "llm", "step": 4,
         "thought": "退款和履约要按订单口径汇总，避免 item 行导致 GMV 被重复计算。", "duration_ms": 1180},
        {"stage": "validate_sql", "title": "校验只读 SQL 与扫描风险", "status": "completed", "kind": "tool", "step": 5,
         "detail": "4 条 SELECT 均通过只读校验；大表使用时间窗和聚合后 join", "duration_ms": 92},
        {"stage": "execute_sql", "title": "执行指标 SQL", "status": "completed", "kind": "sql", "step": 6,
         "sql": "WITH paid_orders AS (...)\nSELECT month, channel, net_revenue, refund_rate, on_time_rate\nFROM kpi_rollup\nORDER BY month, net_revenue DESC;",
         "row_count": 24, "duration_ms": 44},
        {"stage": "execute_sql", "title": "绘制图表数据", "status": running, "kind": "tool", "step": 7,
         "detail": "输出趋势、渠道漏斗、退款原因和库存风险四组数据", "duration_ms": 360 if final else 0},
    ]


def _expand_latest_trace(app: QApplication, win: MainWindow, key: str) -> None:
    view = win.ask_tab.view(key)
    if view is None:
        return
    turn = _find_latest_turn(view)
    if turn is None:
        return
    turn._toggle_trace()
    if _wait_for_trace_drawer(app, win):
        panel = getattr(win, "_trace_drawer_panel", None)
        if panel is not None:
            panel.relayout(animate=False, raise_panel=True)
    _process(app, 4)


def show_assets_initializing(app: QApplication, win: MainWindow) -> Path:
    win.switch_tab("Workbench")
    win.sidebar.context_tabs.setCurrentIndex(1)
    win.schema_rows = _partial_schema_rows(win.schema_rows, table_count=9)
    win.sidebar.load_schema(win.schema_rows)
    win.sidebar.start_build_progress("正在构建资产 · omni_shop")
    win.sidebar.update_build_progress({
        "title": "building table docs",
        "database": "main",
        "total_tables": len(TABLES),
        "completed_tables": 9,
        "current_table": "refunds",
    })
    _expand_schema_tree(win, database_only=True)
    _process(app, 8)
    return _grab(app, win, "01-assets-initializing")


def show_runtime_thinking(app: QApplication, win: MainWindow) -> Path:
    _ensure_chat_visible(win)
    win.sidebar.finish_build_progress("构建完成")
    win.ask_tab.set_has_connection(True)
    key = "promo-thinking"
    win._active_key = key
    win.ask_tab.set_active(key)
    win.ask_tab.begin_turn(
        key,
        "帮我定位 5 月净收入下滑的根因：要同时考虑渠道投放、退款、履约延迟和库存缺货，先给出可验证的分析路径。",
        connection="omni_shop",
        database="main",
        attachments=[
            {"kind": "database", "name": "main", "path": "omni_shop.main"},
            {"kind": "table", "name": "orders", "path": "omni_shop.main.orders"},
            {"kind": "table", "name": "refunds", "path": "omni_shop.main.refunds"},
        ],
    )
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": "已把问题拆成四个可验证链路：收入口径、退款口径、履约口径、库存可售口径。下一步会逐条执行 SQL 并汇总证据。",
        "trace": _trace_events(final=False),  # last step still running → live-looking timeline
        "workflow_id": "wf_promo_thinking",
    })
    _expand_latest_trace(app, win, key)
    _process(app, 12)
    panel = getattr(win, "_trace_drawer_panel", None)
    if panel is None or not panel.isVisible():
        raise RuntimeError("trace drawer did not open for 02-runtime-thinking")
    return _grab_trimmed(app, panel, "02-runtime-thinking")


def show_trace_timeline(app: QApplication, win: MainWindow) -> Path:
    _ensure_chat_visible(win)
    win.ask_tab.set_has_connection(True)
    key = "promo-trace"
    win._active_key = key
    win.ask_tab.set_active(key)
    win.ask_tab.begin_turn(
        key,
        "开发排障：orders、payments、refunds、ledger_entries 四张表做一致性校验，给出异常类型和修复优先级。",
        connection="omni_shop",
        database="main",
        attachments=[
            {"kind": "table", "name": "orders", "path": "omni_shop.main.orders"},
            {"kind": "table", "name": "payments", "path": "omni_shop.main.payments"},
            {"kind": "table", "name": "refunds", "path": "omni_shop.main.refunds"},
            {"kind": "table", "name": "ledger_entries", "path": "omni_shop.main.ledger_entries"},
        ],
    )
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": "已完成订单级聚合、支付/退款/总账差异比对，并按 missing ledger、duplicate ledger、cancelled paid、refund without item 四类分桶。",
        "trace": _developer_consistency_trace(),
        "workflow_id": "wf_promo_trace",
    })
    _expand_latest_trace(app, win, key)
    _process(app, 12)
    panel = getattr(win, "_trace_drawer_panel", None)
    if panel is None or not panel.isVisible():
        raise RuntimeError("trace drawer did not open for 17-agent-trace")
    return _grab_trimmed(app, panel, "17-agent-trace")


def _promo_chart_answer_payload() -> dict[str, Any]:
    answer = (
        "## 结论摘要\n\n"
        "5 月净收入环比下降 **10.5%**（177.5 万 → 158.8 万）。主因不是流量萎缩，而是 **直播渠道退款率抬升** "
        "与 **华南前置仓缺货导致履约延迟** 叠加。\n\n"
        "**关键发现**\n"
        "- 3–5 月 GMV 仍增长 7.8%，但退款金额增长 34.6%，净收入被持续侵蚀。\n"
        "- 直播渠道贡献了 41% 的新增退款，「未按时送达」「质量问题」占比最高。\n"
        "- 缺货 SKU 与退款商品重合度高，库存与履约是最可操作的抓手。\n\n"
        "{{chart:1}}\n\n"
        "## 趋势与双轴拆解\n\n"
        "左轴观察 GMV 与净收入走势，右轴同步查看退款率，便于识别「量增利减」区间。\n\n"
        "{{chart:2}}\n\n"
        "## 渠道结构\n\n"
        "搜索广告净收入稳定；直播净收入 4 月起持续下滑。内容种草 CPA 上升而转化未同步改善。\n\n"
        "{{chart:3}}\n\n"
        "{{chart:4}}\n\n"
        "## 退款、履约与转化\n\n"
        "退款原因与履约指标共同指向「直播爆单 + 仓配承压」的组合风险。\n\n"
        "{{chart:5}}\n\n"
        "{{chart:6}}\n\n"
        "{{chart:7}}\n\n"
        "## 品类热力与库存风险\n\n"
        "{{chart:8}}\n\n"
        "{{chart:9}}\n\n"
        "## 行动建议\n\n"
        "1. 对直播 TOP 20 SKU 设定安全库存，不足时自动降权推荐。\n"
        "2. 将「未按时送达」退款订单回溯到仓库与承运商，优先处理华南前置仓。\n"
        "3. 将退款率纳入投放预算闸门，避免只按 GMV 加预算。\n\n"
        "核心 SQL 已按 **订单粒度先聚合再 join**，避免 order_items 行级放大收入口径。"
    )
    charts = [
        {
            "chart_id": "chart:1", "chart_type": "line", "title": "GMV / 净收入 / 退款率趋势",
            "categories": ["2026-03", "2026-04", "2026-05"],
            "series": [
                {"name": "GMV(万元)", "values": [188.4, 204.8, 203.1]},
                {"name": "净收入(万元)", "values": [169.2, 177.5, 158.8]},
                {"name": "退款率(%)", "values": [5.8, 8.4, 12.7]},
            ],
            "x_label": "月份", "y_label": "指标", "row_count": 3,
        },
        {
            "chart_id": "chart:2", "chart_type": "combo", "title": "净收入与退款率（双轴）",
            "categories": ["2026-03", "2026-04", "2026-05"],
            "series": [
                {"name": "净收入(万元)", "values": [169.2, 177.5, 158.8], "type": "bar", "axis": "left"},
                {"name": "退款率(%)", "values": [5.8, 8.4, 12.7], "type": "line", "axis": "right"},
            ],
            "axes": {
                "left": {"label": "净收入", "format": "number"},
                "right": {"label": "退款率", "format": "percent"},
            },
            "row_count": 3,
        },
        {
            "chart_id": "chart:3", "chart_type": "stacked_area", "title": "渠道净收入构成（堆叠）",
            "categories": ["2026-03", "2026-04", "2026-05"],
            "series": [
                {"name": "搜索广告", "values": [38.2, 41.0, 42.5], "type": "area"},
                {"name": "直播", "values": [36.8, 34.5, 31.2], "type": "area"},
                {"name": "内容种草", "values": [24.1, 25.6, 26.8], "type": "area"},
                {"name": "自然流量", "values": [22.4, 23.0, 24.1], "type": "area"},
            ],
            "row_count": 3,
        },
        {
            "chart_id": "chart:4", "chart_type": "bar", "title": "5 月渠道净收入与退款率",
            "categories": ["搜索广告", "直播", "内容种草", "自然流量", "私域", "联盟"],
            "series": [
                {"name": "净收入(万元)", "values": [42.5, 31.2, 26.8, 24.1, 19.7, 14.5]},
                {"name": "退款率(%)", "values": [6.2, 18.9, 13.1, 7.4, 4.8, 8.5]},
            ],
            "x_label": "渠道", "y_label": "值", "row_count": 6,
        },
        {
            "chart_id": "chart:5", "chart_type": "donut", "title": "5 月退款原因构成",
            "categories": ["未按时送达", "质量问题", "尺码不合适", "七天无理由", "其他"],
            "series": [{"name": "退款笔数", "values": [142, 98, 76, 54, 31]}],
            "row_count": 5,
        },
        {
            "chart_id": "chart:6", "chart_type": "funnel", "title": "直播渠道转化漏斗",
            "categories": ["曝光", "点击", "加购", "下单", "支付成功"],
            "series": [{"name": "用户数", "values": [82000, 24600, 9800, 4200, 3610]}],
            "options": {"sort_order": "descending"},
            "row_count": 5,
        },
        {
            "chart_id": "chart:7", "chart_type": "gauge", "title": "5 月准时履约率",
            "options": {"gauge_min": 0, "gauge_max": 100, "gauge_target": 95},
            "data": {"value": 87.6, "name": "准时履约率(%)"},
        },
        {
            "chart_id": "chart:8", "chart_type": "heatmap", "title": "品类 × 渠道退款强度",
            "data": {
                "x_categories": ["搜索广告", "直播", "内容种草", "自然流量"],
                "y_categories": ["护肤", "智能配件", "厨房", "露营"],
                "points": [
                    [0, 0, 6.2], [1, 0, 18.4], [2, 0, 12.1], [3, 0, 7.0],
                    [0, 1, 5.8], [1, 1, 16.2], [2, 1, 11.4], [3, 1, 6.5],
                    [0, 2, 4.1], [1, 2, 9.8], [2, 2, 8.2], [3, 2, 5.3],
                    [0, 3, 3.6], [1, 3, 7.4], [2, 3, 6.1], [3, 3, 4.8],
                ],
            },
        },
        {
            "chart_id": "chart:9", "chart_type": "horizontal_bar", "title": "库存风险 SKU（缺口件数）",
            "categories": ["SKU-00017", "SKU-00042", "SKU-00009", "SKU-00058", "SKU-00031"],
            "series": [{"name": "缺口件数", "values": [420, 360, 310, 260, 220]}],
            "x_label": "缺口", "y_label": "SKU", "row_count": 5,
        },
    ]
    sql = (
        "WITH paid AS (\n"
        "  SELECT o.id, strftime('%Y-%m', o.ordered_at) AS month, o.channel,\n"
        "         o.gross_amount - o.discount_amount + o.shipping_fee AS paid_amount\n"
        "  FROM orders o JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'\n"
        "), refund AS (\n"
        "  SELECT order_id, SUM(amount) AS refund_amount FROM refunds WHERE status = 'approved' GROUP BY order_id\n"
        ")\n"
        "SELECT month, channel, SUM(paid_amount - COALESCE(refund_amount,0)) AS net_revenue,\n"
        "       SUM(COALESCE(refund_amount,0)) / SUM(paid_amount) AS refund_rate\n"
        "FROM paid LEFT JOIN refund ON refund.order_id = paid.id\n"
        "GROUP BY month, channel;"
    )
    return {
        "question": "从 3–5 月看，哪些因素导致净收入下滑？请给出 SQL 证据、趋势图、渠道拆解和可执行建议。",
        "attachments": [
            {"kind": "database", "name": "main", "path": "omni_shop.main"},
            {"kind": "table", "name": "orders", "path": "omni_shop.main.orders"},
            {"kind": "table", "name": "payments", "path": "omni_shop.main.payments"},
            {"kind": "table", "name": "refunds", "path": "omni_shop.main.refunds"},
        ],
        "answer_markdown": answer,
        "charts": charts,
        "selected_sql": sql,
    }


def show_chart_answer(app: QApplication, win: MainWindow) -> tuple[Path, Path]:
    _ensure_chat_visible(win)
    key = "promo-answer"
    win._active_key = key
    win.ask_tab.set_active(key)
    payload = _promo_chart_answer_payload()
    win.ask_tab.begin_turn(
        key,
        str(payload["question"]),
        connection="omni_shop",
        database="main",
        attachments=payload["attachments"],
    )
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": payload["answer_markdown"],
        "charts": payload["charts"],
        "selected_sql": payload["selected_sql"],
        "trace": _trace_events(final=True),
        "workflow_id": "wf_promo_root_cause",
    })
    view = win.ask_tab.view(key)
    chart_count = len(payload["charts"])
    _wait_for_answer_charts(app, win, key, chart_count=chart_count)
    if view is not None:
        first = _grab_scrolled(app, view, win.ask_tab, "03-chart-answer-analysis", 0.0)
    else:
        first = _grab(app, win.ask_tab, "03-chart-answer-analysis")
    if view is not None:
        second = _grab_scrolled(app, view, win.ask_tab, "04-chart-answer-breakdown", 0.44)
    else:
        second = _grab(app, win.ask_tab, "04-chart-answer-breakdown")
    return first, second


def _developer_field_trace() -> list[dict[str, Any]]:
    return [
        {"stage": "loop", "title": "启动结构核查", "status": "completed", "kind": "phase", "step": 1,
         "thought": "用户给出的 refund_amount 可能是业务口径名，不一定是物理字段；先不要直接写 SQL。",
         "duration_ms": 140},
        {"stage": "discover_schema", "title": "搜索字段 refund_amount", "status": "completed", "kind": "tool", "step": 2,
         "detail": "全库未发现精确字段 refund_amount；发现 refunds.amount、ledger_entries.amount 两个候选金额字段",
         "duration_ms": 510},
        {"stage": "retrieve_schema_context", "title": "读取 refunds 表结构", "status": "completed", "kind": "subagent",
         "parent": "discover_schema", "node_id": "schema/refunds",
         "detail": "refunds(id, order_id, item_id, requested_at, reason, amount, status)"},
        {"stage": "retrieve_schema_context", "title": "读取 ledger_entries 表结构", "status": "completed", "kind": "subagent",
         "parent": "discover_schema", "node_id": "schema/ledger",
         "detail": "ledger_entries(order_id, payment_id, refund_id, entry_type, amount)"},
        {"stage": "retrieve_join_context", "title": "确认关联路径", "status": "completed", "kind": "tool", "step": 3,
         "detail": "refunds.id = ledger_entries.refund_id；refunds.order_id = orders.id", "duration_ms": 270},
        {"stage": "generate_sql", "title": "改写 SQL", "status": "completed", "kind": "llm", "step": 4,
         "thought": "用 refunds.amount 表示退款申请金额；如果要对账，则使用 ledger_entries.amount 且 entry_type='refund'。",
         "duration_ms": 760},
        {"stage": "validate_sql", "title": "校验字段和只读 SQL", "status": "completed", "kind": "tool", "step": 5,
         "detail": "SELECT 只读；所有字段均存在；join path 已验证", "duration_ms": 58},
    ]


def show_developer_field_exploration(app: QApplication, win: MainWindow) -> Path:
    _ensure_chat_visible(win)
    key = "promo-dev-field"
    win._active_key = key
    win.ask_tab.set_active(key)
    win.ask_tab.begin_turn(
        key,
        "开发排查：refunds 表里是不是有 refund_amount 字段？如果没有，自动探索正确字段、关联路径，并给出可执行 SQL。",
        connection="omni_shop",
        database="main",
        attachments=[
            {"kind": "table", "name": "refunds", "path": "omni_shop.main.refunds"},
            {"kind": "table", "name": "ledger_entries", "path": "omni_shop.main.ledger_entries"},
        ],
    )
    sql = (
        "SELECT r.id AS refund_id, r.order_id, r.amount AS refund_request_amount,\n"
        "       ABS(COALESCE(l.amount, 0)) AS ledger_refund_amount,\n"
        "       r.status, r.reason, r.requested_at\n"
        "FROM refunds r\n"
        "LEFT JOIN ledger_entries l ON l.refund_id = r.id AND l.entry_type = 'refund'\n"
        "WHERE r.requested_at >= '2026-05-01'\n"
        "ORDER BY r.requested_at DESC;"
    )
    answer = (
        "## 字段核查结果\n\n"
        "**`refunds.refund_amount` 不存在。** Agent 未按错误字段硬写 SQL，而是先搜索字段、读取表结构、"
        "确认 join path，再自动改写为可执行版本。\n\n"
        "| 目标 | 发现 | 说明 |\n"
        "|---|---|---|\n"
        "|退款申请金额|`refunds.amount`|退款业务表里的金额字段|\n"
        "|退款入账金额|`ledger_entries.amount`|总账表里的金额，退款通常为负数|\n"
        "|关联路径|`refunds.id = ledger_entries.refund_id`|可用于校验申请金额与入账金额|\n\n"
        "推荐在开发排障时同时对比 `refunds.amount` 与 `ledger_entries.amount`，"
        "避免把申请金额误当作入账金额。"
    )
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": answer,
        "selected_sql": sql,
        "trace": _developer_field_trace(),
        "workflow_id": "wf_promo_field_explore",
    })
    _expand_latest_trace(app, win, key)
    _wait_for_answer_document(app, win, key)
    _process(app, 8)
    return _grab_trimmed(app, win.ask_tab, "08-developer-field-exploration")


def _developer_consistency_trace() -> list[dict[str, Any]]:
    return [
        {"stage": "loop", "title": "制定一致性校验计划", "status": "completed", "kind": "phase", "step": 1,
         "thought": "这是开发排障任务，不需要澄清；按订单、支付、退款、总账四个事实源交叉验证。",
         "duration_ms": 180},
        {"stage": "discover_schema", "title": "发现账务相关表", "status": "completed", "kind": "tool", "step": 2,
         "detail": "orders, payments, refunds, ledger_entries, order_items, shipments", "duration_ms": 430},
        {"stage": "retrieve_join_context", "title": "建立校验 join graph", "status": "completed", "kind": "tool", "step": 3,
         "detail": "orders.id -> payments.order_id/refunds.order_id/ledger_entries.order_id；refunds.id -> ledger_entries.refund_id",
         "duration_ms": 390},
        {"stage": "generate_sql", "title": "生成订单级对账 SQL", "status": "completed", "kind": "llm", "step": 4,
         "thought": "所有金额先聚合到 order_id，再做差异比较，避免 order_items 多行放大。",
         "duration_ms": 980},
        {"stage": "execute_sql", "title": "支付 vs 订单金额", "status": "completed", "kind": "sql", "step": 5,
         "sql": "SELECT order_id, paid_amount, expected_amount, paid_amount - expected_amount AS diff FROM payment_check WHERE ABS(diff) > 0.01",
         "row_count": 18, "duration_ms": 41},
        {"stage": "execute_sql", "title": "退款 vs 总账退款", "status": "completed", "kind": "sql", "step": 6,
         "sql": "SELECT refund_id, refund_amount, ledger_amount, reason FROM refund_ledger_check WHERE ABS(diff) > 0.01",
         "row_count": 37, "duration_ms": 39},
        {"stage": "execute_sql", "title": "异常分桶归因", "status": "completed", "kind": "tool", "step": 7,
         "detail": "按 missing_ledger、duplicate_ledger、cancelled_paid、refund_without_item 四类聚合",
         "duration_ms": 76},
        {"stage": "validate_sql", "title": "输出可复核 SQL 与修复建议", "status": "completed", "kind": "tool", "step": 8,
         "detail": "所有查询为只读 SELECT；结果限制到异常样本和聚合分桶", "duration_ms": 54},
    ]


def _promo_consistency_audit_payload() -> dict[str, Any]:
    answer = (
        "## 一致性校验结论\n\n"
        "已对 `orders`、`payments`、`refunds`、`ledger_entries` 四张表完成 **订单粒度** 交叉对账。"
        "共识别 **4 类可复现异常**，合计影响 **48 笔订单**。\n\n"
        "| 异常类型 | 订单数 | 主要原因 | 建议动作 |\n"
        "|---|---:|---|---|\n"
        "|退款已批准但无总账退款|21|`refunds.status='approved'` 后未写入 `ledger_entries`|补偿写账或回滚退款状态|\n"
        "|同一退款重复入账|9|`ledger_entries.refund_id` 出现重复|增加唯一约束或幂等键|\n"
        "|取消订单存在成功支付|6|取消流程晚于支付回调|检查状态机与回调顺序|\n"
        "|退款挂订单但未挂 item|12|部分退款缺少 `item_id`|补齐 item 级退款明细|\n\n"
        "Agent 先自动探索字段与 join graph，再按 `order_id` 聚合到同一粒度做差异比较，"
        "最后按异常特征分桶归因。\n\n"
        "{{chart:1}}\n\n"
        "{{chart:2}}\n\n"
        "下图展示订单资金从应付 → 支付 → 总账 → 退款的主链路，便于定位断点环节。\n\n"
        "{{chart:3}}\n\n"
        "下方 SQL 为只读校验语句，可直接在客户端复核异常样本。"
    )
    charts = [
        {
            "chart_id": "chart:1", "chart_type": "bar", "title": "四类异常订单数量",
            "categories": ["退款无总账", "重复入账", "取消仍支付", "退款缺 item"],
            "series": [{"name": "订单数", "values": [21, 9, 6, 12]}],
            "row_count": 4,
        },
        {
            "chart_id": "chart:2", "chart_type": "donut", "title": "异常类型占比",
            "categories": ["退款无总账", "重复入账", "取消仍支付", "退款缺 item"],
            "series": [{"name": "订单数", "values": [21, 9, 6, 12]}],
            "row_count": 4,
        },
        {
            "chart_id": "chart:3", "chart_type": "sankey", "title": "订单资金流向校验",
            "data": {
                "nodes": [
                    {"name": "订单应付"},
                    {"name": "支付入账"},
                    {"name": "总账支付"},
                    {"name": "退款申请"},
                    {"name": "总账退款"},
                ],
                "links": [
                    {"source": "订单应付", "target": "支付入账", "value": 1842},
                    {"source": "支付入账", "target": "总账支付", "value": 1818},
                    {"source": "订单应付", "target": "退款申请", "value": 248},
                    {"source": "退款申请", "target": "总账退款", "value": 229},
                ],
            },
        },
    ]
    sql = (
        "WITH order_money AS (\n"
        "  SELECT id AS order_id, gross_amount - discount_amount + shipping_fee AS expected_paid\n"
        "  FROM orders WHERE status <> 'cancelled'\n"
        "), pay AS (\n"
        "  SELECT order_id, SUM(amount) AS paid_amount FROM payments WHERE status='succeeded' GROUP BY order_id\n"
        "), refund AS (\n"
        "  SELECT order_id, SUM(amount) AS refund_amount FROM refunds WHERE status='approved' GROUP BY order_id\n"
        "), ledger AS (\n"
        "  SELECT order_id,\n"
        "         SUM(CASE WHEN entry_type='payment' THEN amount ELSE 0 END) AS ledger_paid,\n"
        "         SUM(CASE WHEN entry_type='refund' THEN -amount ELSE 0 END) AS ledger_refund\n"
        "  FROM ledger_entries GROUP BY order_id\n"
        ")\n"
        "SELECT o.order_id, expected_paid, paid_amount, ledger_paid, refund_amount, ledger_refund,\n"
        "       ROUND(COALESCE(paid_amount,0) - COALESCE(ledger_paid,0), 2) AS pay_ledger_diff,\n"
        "       ROUND(COALESCE(refund_amount,0) - COALESCE(ledger_refund,0), 2) AS refund_ledger_diff\n"
        "FROM order_money o\n"
        "LEFT JOIN pay p USING(order_id)\n"
        "LEFT JOIN refund r USING(order_id)\n"
        "LEFT JOIN ledger l USING(order_id)\n"
        "WHERE ABS(COALESCE(paid_amount,0) - COALESCE(ledger_paid,0)) > 0.01\n"
        "   OR ABS(COALESCE(refund_amount,0) - COALESCE(ledger_refund,0)) > 0.01;"
    )
    return {
        "question": (
            "开发排障：自动校验 orders、payments、refunds、ledger_entries 的金额一致性；"
            "找出不一致订单，并继续探索不一致原因。"
        ),
        "attachments": [
            {"kind": "table", "name": "orders", "path": "omni_shop.main.orders"},
            {"kind": "table", "name": "payments", "path": "omni_shop.main.payments"},
            {"kind": "table", "name": "refunds", "path": "omni_shop.main.refunds"},
            {"kind": "table", "name": "ledger_entries", "path": "omni_shop.main.ledger_entries"},
        ],
        "answer_markdown": answer,
        "charts": charts,
        "selected_sql": sql,
    }


def _dependency_tree_trace() -> list[dict[str, Any]]:
    return [
        {"stage": "loop", "title": "解析依赖意图", "status": "completed", "kind": "phase", "step": 1,
         "thought": "目标是以 orders 为根，重建外键依赖树（上游维度 + 下游事实）。", "duration_ms": 160},
        {"stage": "discover_schema", "title": "遍历外键图", "status": "completed", "kind": "tool", "step": 2,
         "detail": "扫描 24 张表 / 37 条外键，定位 orders 的入边与出边", "duration_ms": 540},
        {"stage": "retrieve_join_context", "title": "重建层级", "status": "completed", "kind": "tool", "step": 3,
         "detail": "orders→order_items→products→categories/brands；orders→payments/refunds→ledger_entries", "duration_ms": 610},
        {"stage": "generate_sql", "title": "标注关键节点", "status": "completed", "kind": "llm", "step": 4,
         "thought": "ledger_entries 同时被 payments 与 refunds 引用，标为资金核验汇聚点。", "duration_ms": 430},
    ]


def _promo_dependency_tree_payload() -> dict[str, Any]:
    answer = (
        "## 依赖树重建完成\n\n"
        "已自动遍历 **24 张表 / 37 条外键**，以 `orders` 为根重建依赖树：上游维度、下游事实与资金链路"
        "一张图看清——不用手翻 DDL。\n\n"
        "**关键发现**\n"
        "- `orders` 是 **6 条关联路径** 的交汇点，是整个 schema 的事实中枢。\n"
        "- `ledger_entries` 同时挂在 `payments` 与 `refunds` 之下，是对账与资金核验的关键汇聚点。\n"
        "- `products → categories / brands` 构成商品维度子树，按类目/品牌下钻就走这条路径。\n\n"
        "{{chart:1}}\n\n"
        "## 工程提示\n\n"
        "- 改 `orders` 结构前，先评估这 6 条下游链路的影响面。\n"
        "- 资金相关查询务必经过 `ledger_entries`，只查 `payments` 会漏掉退款侧。"
    )
    charts = [{
        "chart_id": "chart:1",
        "chart_type": "tree",
        "title": "orders 外键依赖树",
        "data": {"tree": [{
            "name": "orders",
            "children": [
                {"name": "order_items", "children": [
                    {"name": "products", "children": [
                        {"name": "categories"},
                        {"name": "brands"},
                    ]},
                ]},
                {"name": "payments", "children": [{"name": "ledger_entries"}]},
                {"name": "refunds", "children": [{"name": "ledger_entries"}]},
                {"name": "shipments", "children": [{"name": "warehouses"}]},
                {"name": "users", "children": [{"name": "addresses"}]},
                {"name": "coupons"},
            ],
        }]},
        "row_count": 11,
    }]
    return {
        "question": (
            "开发：把核心事实表 orders 的外键依赖梳理成一棵依赖树——上游依赖哪些维度表、"
            "下游又被哪些表引用，并标出资金链路的关键节点。"
        ),
        "attachments": [
            {"kind": "table", "name": "orders", "path": "omni_shop.main.orders"},
            {"kind": "table", "name": "payments", "path": "omni_shop.main.payments"},
            {"kind": "table", "name": "ledger_entries", "path": "omni_shop.main.ledger_entries"},
        ],
        "answer_markdown": answer,
        "charts": charts,
    }


def show_developer_dependency_tree(app: QApplication, win: MainWindow) -> Path:
    _ensure_chat_visible(win)
    key = "promo-dev-tree"
    win._active_key = key
    win.ask_tab.set_active(key)
    payload = _promo_dependency_tree_payload()
    win.ask_tab.begin_turn(
        key,
        str(payload["question"]),
        connection="omni_shop",
        database="main",
        attachments=payload["attachments"],
    )
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": payload["answer_markdown"],
        "charts": payload["charts"],
        "trace": _dependency_tree_trace(),
        "workflow_id": "wf_promo_dependency_tree",
    })
    view = win.ask_tab.view(key)
    _wait_for_answer_charts(app, win, key, chart_count=len(payload["charts"]))
    if view is not None:
        return _grab_scrolled(app, view, win.ask_tab, "18-developer-dependency-tree", 0.34)
    return _grab(app, win.ask_tab, "18-developer-dependency-tree")


def show_developer_consistency_audit(app: QApplication, win: MainWindow) -> Path:
    _ensure_chat_visible(win)
    key = "promo-dev-audit"
    win._active_key = key
    win.ask_tab.set_active(key)
    payload = _promo_consistency_audit_payload()
    win.ask_tab.begin_turn(
        key,
        str(payload["question"]),
        connection="omni_shop",
        database="main",
        attachments=payload["attachments"],
    )
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": payload["answer_markdown"],
        "charts": payload["charts"],
        "selected_sql": payload["selected_sql"],
        "trace": _developer_consistency_trace(),
        "workflow_id": "wf_promo_consistency_audit",
    })
    view = win.ask_tab.view(key)
    _wait_for_answer_charts(app, win, key, chart_count=len(payload["charts"]))
    if view is not None:
        return _grab_scrolled(app, view, win.ask_tab, "09-developer-consistency-audit", 0.0)
    return _grab(app, win.ask_tab, "09-developer-consistency-audit")


def show_clarification(app: QApplication, win: MainWindow) -> Path:
    _ensure_chat_visible(win)
    key = "promo-clarify"
    win._active_key = key
    win.ask_tab.set_active(key)
    win.ask_tab.begin_turn(
        key,
        "帮我核对订单、支付、退款、总账是否一致；不一致的给出明细和修复建议。",
        connection="omni_shop",
        database="main",
    )
    win.ask_tab.append_result(key, {
        "status": "wait_user",
        "pending_question": "这个一致性校验会影响结论口径，我需要先确认三件事：",
        "pending_questions": [
            {"ask": "退款金额以 refunds.requested_at 还是 ledger_entries.entry_at 归属月份？",
             "options": ["按退款申请时间", "按总账入账时间"]},
            {"ask": "取消订单是否纳入支付-订单一致性校验？",
             "options": ["排除 cancelled", "纳入全部订单"]},
            {"ask": "差异阈值使用多少？",
             "options": ["0.01 元", "1 元", "10 元"]},
        ],
    })
    _process(app, 8)
    return _grab_trimmed(app, win.ask_tab, "05-clarification")


def show_database_client(app: QApplication, win: MainWindow) -> tuple[Path, Path]:
    win.switch_tab("Workbench")
    full_rows = win.service.dispatch("schema_tree", {"name": "omni_shop"})
    win.schema_rows = full_rows
    win.sidebar.load_schema(full_rows)
    win.sidebar.finish_build_progress("构建完成")
    _expand_schema_tree(win)
    sql = (
        "SELECT c.name AS category,\n"
        "       COUNT(DISTINCT o.id) AS orders,\n"
        "       ROUND(SUM(oi.quantity * oi.unit_price - oi.item_discount), 2) AS item_revenue,\n"
        "       ROUND(SUM(COALESCE(r.amount, 0)), 2) AS refund_amount,\n"
        "       ROUND(SUM(COALESCE(r.amount, 0)) / NULLIF(SUM(oi.quantity * oi.unit_price), 0), 4) AS refund_rate\n"
        "FROM order_items oi\n"
        "JOIN orders o ON o.id = oi.order_id\n"
        "JOIN products p ON p.id = oi.product_id\n"
        "JOIN categories c ON c.id = p.category_id\n"
        "LEFT JOIN refunds r ON r.item_id = oi.id OR r.order_id = o.id\n"
        "WHERE o.ordered_at >= '2026-05-01' AND o.status <> 'cancelled'\n"
        "GROUP BY c.name\n"
        "ORDER BY refund_rate DESC;"
    )
    editor = win.workbench.open_sql(sql)
    editor.show_result({
        "columns": ["category", "orders", "item_revenue", "refund_amount", "refund_rate"],
        "rows": [
            {"category": "护肤", "orders": 188, "item_revenue": 361240.0, "refund_amount": 56320.0, "refund_rate": 0.156},
            {"category": "智能配件", "orders": 212, "item_revenue": 298450.0, "refund_amount": 38200.0, "refund_rate": 0.128},
            {"category": "厨房", "orders": 145, "item_revenue": 226190.0, "refund_amount": 20980.0, "refund_rate": 0.093},
            {"category": "露营", "orders": 119, "item_revenue": 194820.0, "refund_amount": 15120.0, "refund_rate": 0.078},
        ],
        "row_count": 4,
        "elapsed_ms": 37,
        "sql": sql,
        "table": "category_refund_rollup",
        "dialect": "sqlite",
    })
    _process(app, 8)
    sql_path = _grab(app, win, "06-database-client-sql")

    node = win._find_table_node("orders", database="main")
    if node is not None:
        win.open_schema_asset(node)
        doc = win.workbench.tabs.currentWidget()
        if doc is not None and hasattr(doc, "data"):
            doc.focus_data()
            doc.show_result({
                "columns": ["id", "order_no", "user_id", "ordered_at", "status", "channel", "gross_amount", "discount_amount"],
                "rows": [
                    {"id": 2279, "order_no": "EC260530002279", "user_id": 87, "ordered_at": "2026-05-30T13:45:00", "status": "delivered", "channel": "直播", "gross_amount": 1267.0, "discount_amount": 126.7},
                    {"id": 2280, "order_no": "EC260530002280", "user_id": 142, "ordered_at": "2026-05-30T13:48:00", "status": "refunded", "channel": "内容种草", "gross_amount": 618.0, "discount_amount": 30.9},
                    {"id": 2281, "order_no": "EC260530002281", "user_id": 31, "ordered_at": "2026-05-30T14:02:00", "status": "paid", "channel": "搜索广告", "gross_amount": 398.0, "discount_amount": 0.0},
                    {"id": 2282, "order_no": "EC260530002282", "user_id": 209, "ordered_at": "2026-05-30T14:08:00", "status": "delivered", "channel": "私域", "gross_amount": 897.0, "discount_amount": 88.0},
                ],
                "row_count": 4,
                "elapsed_ms": 22,
                "table": "orders",
                "database": "main",
                "page_size": 100,
                "offset": 0,
                "has_more": True,
            })
    _process(app, 8)
    data_path = _grab(app, win, "07-database-client-table")
    return sql_path, data_path


def _connection_payloads(service: DesktopService) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for conn in service.cfg.connections().values():
        target = conn.path or f"{conn.host}:{conn.port or ''}/{conn.database or ''}".strip("/")
        out.append({
            "name": conn.name,
            "type": conn.type,
            "path": conn.path,
            "host": conn.host,
            "port": conn.port,
            "database": conn.database,
            "user": conn.user,
            "has_password": bool(conn.password or conn.password_env),
            "load_profile": conn.load_profile,
            "session_timezone": conn.session_timezone,
            "sslmode": conn.sslmode,
            "ssl_ca": conn.ssl_ca,
            "target": target,
            "asset_status": "ready",
        })
    return out


def _model_payloads(service: DesktopService) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for model in service.cfg.models().values():
        out.append({
            "name": model.name,
            "provider": model.provider,
            "base_url": model.base_url,
            "model": model.model,
            "timeout_seconds": model.timeout_seconds,
            "context_length": model.context_length,
            "has_api_key": bool(model.api_key or model.api_key_env),
        })
    return out


def _build_settings_dialog(service: DesktopService, *, initial_page: str) -> SettingsDialog:
    dialog = SettingsDialog(
        connections=_connection_payloads(service),
        models=_model_payloads(service),
        default_connection=service.cfg.get_connection(None).name,
        default_model=service.cfg.model().name,
        resource_defaults=service.resource_defaults(),
        language="zh",
        stream_answers=True,
        debug_trace=True,
        initial_page=initial_page,
    )
    dialog.resize(1020, 700)
    dialog.show()
    if initial_page == "connections":
        dialog.conn_list.setCurrentRow(0)
    if initial_page == "models":
        dialog.model_list.setCurrentRow(0)
    _process(QApplication.instance() or QApplication([sys.argv[0] or "shoot_promo"]), 8)
    return dialog


def _prepare_integrations_demo() -> None:
    import dbaide.skill as skill

    home = TMP / "skill-home"
    home.mkdir(parents=True, exist_ok=True)
    skill._HOME = home  # type: ignore[attr-defined]
    for tool in ("claude", "codex", "cursor"):
        try:
            skill.uninstall_tool(tool)
        except Exception:
            pass
    skill.setup_tool("claude", mode="ask")
    skill.setup_tool("codex", mode="tools")
    skill.setup_tool("cursor", mode="full")


def show_settings_pages(app: QApplication, service: DesktopService) -> list[Path]:
    _prepare_integrations_demo()
    paths: list[Path] = []
    pages = [
        ("connections", "10-settings-connections"),
        ("models", "11-settings-models"),
        ("resources", "12-settings-resources"),
        ("integrations", "13-settings-integrations"),
    ]
    for page, name in pages:
        dialog = _build_settings_dialog(service, initial_page=page)
        paths.append(_grab(app, dialog, name))
        dialog.close()
        _process(app, 3)
    return paths


def show_settings_page(app: QApplication, service: DesktopService, *, page: str, name: str) -> Path:
    _prepare_integrations_demo()
    dialog = _build_settings_dialog(service, initial_page=page)
    path = _grab(app, dialog, name)
    dialog.close()
    _process(app, 2)
    return path


def show_backup_and_setup(app: QApplication, service: DesktopService) -> list[Path]:
    from dbaide.backup import registry as backup_registry

    backup_registry._DEFAULT_DIR = TMP / "backups"  # type: ignore[attr-defined]
    service.backup_run({
        "connection_name": "omni_shop",
        "database": "main",
        "table": "orders",
        "scope": "table",
        "format": "csv",
        "batch_size": 2000,
    })

    manager = BackupManager(service=service)
    manager.resize(980, 520)
    manager.show()
    _process(app, 8)
    manager_path = _grab(app, manager, "14-backup-manager")
    manager.close()

    build = BuildAssetsDialog(
        connection_name="omni_shop",
        databases=[{"name": "main", "has_assets": True}, {"name": "analytics", "has_assets": False}],
        default_max_workers=2,
    )
    build.show()
    _process(app, 8)
    build_path = _grab(app, build, "15-build-assets-dialog")
    build.close()

    conn = ConnectionDialog(conn_type="postgres")
    conn.form.name.setText("warehouse_pg")
    conn.form.host.setText("analytics.internal")
    conn.form.database.setText("warehouse")
    conn.form.user.setText("readonly_analyst")
    conn.form.session_timezone.setText("+08:00")
    conn.form.sslmode.setCurrentText("require")
    conn.show()
    _process(app, 8)
    conn_path = _grab(app, conn, "16-connection-dialog")
    conn.close()
    return [manager_path, build_path, conn_path]


def write_copy(paths: list[Path]) -> Path:
    copy = OUT / "copy.md"
    copy.write_text(
        "\n".join([
            "# DBAide 宣传截图文案",
            "",
            "## 主标题",
            "DBAide：面向真实数据库的 AI 数据分析与开发工作台",
            "",
            "## 副标题",
            "从资产初始化、结构理解、SQL 生成、风险校验到图表回答，技术人员和业务人员可以在同一个本地优先工作流里协作。",
            "",
            "## 截图与配文",
            "",
            "1. `01-assets-initializing.png`",
            "   资产初始化不再是黑盒：左侧结构树边构建边更新，进度明确到已构建表数与当前表。",
            "",
            "2. `02-runtime-thinking.png`",
            "   复杂问题运行时可见：意图拆解、结构发现、关联校验、SQL 生成与风险检查都能追踪。",
            "",
            "3. `17-agent-trace.png`",
            "   Trace 不再是树状噪音，而是右侧时间线抽屉：步骤、耗时和详情分层查看。",
            "",
            "4. `03-chart-answer-analysis.png`",
            "   业务问题直接给出结构化结论：摘要、关键发现、趋势折线与双轴组合图同屏可读。",
            "",
            "5. `04-chart-answer-breakdown.png`",
            "   回答连续展示多种图表类型：堆叠面积、柱状、环形图、漏斗、仪表盘、热力图与库存风险条。",
            "",
            "6. `05-clarification.png`",
            "   当口径不唯一时先澄清：避免 AI 擅自假设财务归属、取消订单和差异阈值。",
            "",
            "7. `06-database-client-sql.png`",
            "   内置数据库客户端：多标签 SQL 编辑、结果表格、导出、历史和结构树在同一界面，SQL 证据可继续复核。",
            "",
            "8. `07-database-client-table.png`",
            "   表数据浏览与结构查看一体化：适合开发排障，也适合业务同学快速核对明细。",
            "",
            "9. `08-developer-field-exploration.png`",
            "   开发者专项：当字段名不存在时，Agent 会先查字段、读表结构、验证关联路径，再自动改写成可执行 SQL。",
            "",
            "10. `18-developer-dependency-tree.png`",
            "   开发者专项：自动遍历 24 张表 / 37 条外键，以 orders 为根重建外键依赖树（节点-连线树状图），上下游维度与资金链路一张图看清。",
            "",
            "11. `09-developer-consistency-audit.png`",
            "   开发者专项：跨 orders/payments/refunds/ledger_entries 自动对账，表格结论配合柱状、环形与桑基图展示异常分布与资金链路。",
            "",
            "12. `10-settings-connections.png`",
            "    连接管理、导入导出、默认连接切换都在一个面板里完成，便于团队迁移与环境管理。",
            "",
            "13. `11-settings-models.png`",
            "    模型配置与超时、上下文长度、API 凭据分离管理；桌面与 CLI 共享同一套模型配置。",
            "",
            "14. `12-settings-resources.png`",
            "    所有关键资源限制都可配置：SQL 超时、行数上限、Agent 步数、压缩阈值、结果截断长度与并发运行数。",
            "",
            "15. `13-settings-integrations.png`",
            "    MCP / coding tool 集成页可直接安装到 Claude、Codex、Cursor 等工具，并支持 full / ask / tools 三种模式。",
            "",
            "16. `14-backup-manager.png`",
            "    备份管理器统一查看历史备份、格式、行数、大小和文件位置，适合做本地快照与审计留存。",
            "",
            "17. `15-build-assets-dialog.png`",
            "    构建资产支持按库选择、并发与时间预算设置，不必每次重扫整实例。",
            "",
            "18. `16-connection-dialog.png`",
            "    连接表单内置只读负载配置、会话时区和 SSL 选项，便于安全地接入生产或分析库。",
            "",
            "## 面向技术人员",
            "- 看得见 agent 的每一步，便于调试 prompt、SQL、join 推断和性能风险。",
            "- 本地优先连接数据库，内置资源限制、只读校验、EXPLAIN/扫描风险思路。",
            "- 资产层把结构、外键、索引、样本、用户备注沉淀为可复用上下文。",
            "- 开发排障可以从“字段是否存在”一直推进到跨表一致性校验、异常分桶和修复建议。",
            "",
            "## 面向业务人员",
            "- 直接用自然语言问复杂业务问题，不需要先知道表名和 join 关系。",
            "- 图表、结论、SQL 证据同时输出，既能快速决策，也能交给技术复核。",
            "- 遇到口径歧义会主动澄清，减少“看起来合理但口径错误”的结果。",
            "",
            "## 生成的文件",
            *[f"- `{p.name}`" for p in paths],
        ]),
        encoding="utf-8",
    )
    return copy


def main() -> int:
    if OUT.exists():
        for png in OUT.glob("*.png"):
            png.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    _verify_webengine_runtime()
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(ROOT))
    set_language("zh")
    for scenario in DOC_SCENARIOS:
        result = subprocess.run(
            [_docs_python_executable(), str(ROOT / "tools" / "shoot_docs.py"), scenario],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            candidate = line.strip()
            if candidate.endswith(".png"):
                paths.append(Path(candidate))
    copy = write_copy(paths)
    print(f"promo screenshots -> {OUT}")
    print(f"copy -> {copy}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
