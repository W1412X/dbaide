"""Generate promotional screenshots for DBAide.

The script starts the real desktop UI offscreen, seeds a complex ecommerce
SQLite database, builds assets, drives representative assistant/workbench states,
and writes screenshots plus a copy deck to docs/images/promo/.

Usage:
    QT_QPA_PLATFORM=offscreen venv/bin/python tools/shoot_promo.py
"""
from __future__ import annotations

import os
import random
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from dbaide.assets import AssetBuilder, AssetStore
from dbaide.adapters import build_adapter
from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.desktop.theme import app_style
from dbaide.desktop.views.main_window import MainWindow
from dbaide.i18n import set_language
from dbaide.joins import JoinCatalogStore
from dbaide.models import ConnectionConfig, ModelConfig


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images" / "promo"
TMP = Path(tempfile.mkdtemp(prefix="dbaide-promo-"))


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
    db = TMP / "omnichannel_ecommerce.db"
    seed_ecommerce_db(db)
    cfg = ConfigManager(path=TMP / "config.toml")
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
    store = AssetStore(TMP / "assets")
    AssetBuilder(
        connection=conn,
        adapter=build_adapter(conn),
        store=store,
        join_catalog=JoinCatalogStore(base_dir=TMP / "joins"),
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
        app.processEvents()
        QTest.qWait(30)


def _grab(app: QApplication, widget, name: str) -> Path:
    _process(app, 6)
    path = OUT / f"{name}.png"
    widget.grab().save(str(path))
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
    for i in range(view._layout.count() - 1, -1, -1):
        item = view._layout.itemAt(i)
        w = item.widget() if item is not None else None
        if w is not None and hasattr(w, "_toggle_trace"):
            w._toggle_trace()
            break
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
    win.switch_tab("Chat")
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
    for event in _trace_events(final=False):
        win.ask_tab.append_activity_event(key, event)
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": "已把问题拆成四个可验证链路：收入口径、退款口径、履约口径、库存可售口径。下一步会逐条执行 SQL 并汇总证据。",
        "trace": _trace_events(final=True),
        "workflow_id": "wf_promo_thinking",
    })
    _expand_latest_trace(app, win, key)
    _process(app, 8)
    return _grab(app, win, "02-runtime-thinking")


def show_chart_answer(app: QApplication, win: MainWindow) -> tuple[Path, Path]:
    key = "promo-answer"
    win._active_key = key
    win.ask_tab.set_active(key)
    win.ask_tab.begin_turn(
        key,
        "从 3-5 月看，哪些因素导致净收入下滑？请给出 SQL 证据、趋势图、渠道拆解和可执行建议。",
        connection="omni_shop",
        database="main",
        attachments=[
            {"kind": "database", "name": "main", "path": "omni_shop.main"},
            {"kind": "table", "name": "orders", "path": "omni_shop.main.orders"},
            {"kind": "table", "name": "payments", "path": "omni_shop.main.payments"},
            {"kind": "table", "name": "refunds", "path": "omni_shop.main.refunds"},
        ],
    )
    answer = (
        "结论：5 月净收入环比下降主要不是流量问题，而是 **直播渠道退款率上升** 与 **华南前置仓缺货导致履约延迟** 共同造成。\n\n"
        "- 3 月到 5 月 GMV 仍增长 7.8%，但退款金额增长 34.6%，净收入被吃掉。\n"
        "- 直播渠道贡献了 41% 的新增退款，退款原因集中在“未按时送达”和“质量问题”。\n"
        "- 缺货风险最高的 SKU 与退款订单中的商品高度重合，说明库存与履约是可操作抓手。\n\n"
        "{{chart:1}}\n\n"
        "渠道层面看，搜索广告净收入稳定，直播净收入从 4 月开始下滑；同时内容种草的 CPA 上升但转化没有同步增长。\n\n"
        "{{chart:2}}\n\n"
        "建议先做三件事：\n"
        "1. 对直播渠道的 TOP 20 SKU 设定安全库存，不足时自动降权推荐。\n"
        "2. 将“未按时送达”退款订单回溯到仓库和承运商，优先处理华南前置仓。\n"
        "3. 将退款率作为投放预算闸门，避免只按 GMV 加预算。\n\n"
        "下面 SQL 展示了核心口径，按订单先聚合再 join，避免 item 行放大收入。"
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
            "chart_id": "chart:2", "chart_type": "bar", "title": "5 月渠道净收入与退款率",
            "categories": ["搜索广告", "直播", "内容种草", "自然流量", "私域", "联盟"],
            "series": [
                {"name": "净收入(万元)", "values": [42.5, 31.2, 26.8, 24.1, 19.7, 14.5]},
                {"name": "退款率(%)", "values": [6.2, 18.9, 13.1, 7.4, 4.8, 8.5]},
            ],
            "x_label": "渠道", "y_label": "值", "row_count": 6,
        },
        {
            "chart_id": "chart:3", "chart_type": "horizontal_bar", "title": "库存风险 SKU",
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
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": answer,
        "charts": charts,
        "selected_sql": sql,
        "trace": _trace_events(final=True),
        "workflow_id": "wf_promo_root_cause",
    })
    view = win.ask_tab.view(key)
    if view is not None:
        first = _grab_scrolled(app, view, win.ask_tab, "03-chart-answer-analysis", 0.0)
    else:
        first = _grab(app, win.ask_tab, "03-chart-answer-analysis")
    if view is not None:
        second = _grab_scrolled(app, view, win.ask_tab, "04-chart-answer-breakdown", 0.52)
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
    win.switch_tab("Chat")
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
        "**字段核查结果：`refunds.refund_amount` 不存在。**\n\n"
        "| 目标 | 发现 | 说明 |\n"
        "|---|---|---|\n"
        "|退款申请金额|`refunds.amount`|退款业务表里的金额字段|\n"
        "|退款入账金额|`ledger_entries.amount`|总账表里的金额，退款通常为负数|\n"
        "|关联路径|`refunds.id = ledger_entries.refund_id`|可用于校验申请金额与入账金额|\n\n"
        "我没有按不存在的字段硬写 SQL，而是先搜索字段、读取表结构、确认 join path，然后把查询自动修正为下面这个可执行版本。"
    )
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": answer,
        "selected_sql": sql,
        "trace": _developer_field_trace(),
        "workflow_id": "wf_promo_field_explore",
    })
    _expand_latest_trace(app, win, key)
    _process(app, 8)
    return _grab(app, win, "08-developer-field-exploration")


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


def show_developer_consistency_audit(app: QApplication, win: MainWindow) -> Path:
    key = "promo-dev-audit"
    win._active_key = key
    win.ask_tab.set_active(key)
    win.ask_tab.begin_turn(
        key,
        "开发排障：自动校验 orders、payments、refunds、ledger_entries 的金额一致性；找出不一致订单，并继续探索不一致原因。",
        connection="omni_shop",
        database="main",
        attachments=[
            {"kind": "table", "name": "orders", "path": "omni_shop.main.orders"},
            {"kind": "table", "name": "payments", "path": "omni_shop.main.payments"},
            {"kind": "table", "name": "refunds", "path": "omni_shop.main.refunds"},
            {"kind": "table", "name": "ledger_entries", "path": "omni_shop.main.ledger_entries"},
        ],
    )
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
    answer = (
        "**一致性校验完成：发现 4 类可复现异常。**\n\n"
        "| 异常类型 | 数量 | 主要原因 | 下一步 |\n"
        "|---|---:|---|---|\n"
        "|退款已批准但无总账退款|21|`refunds.status='approved'` 后未写入 `ledger_entries`|补偿写账或回滚退款状态|\n"
        "|同一退款重复入账|9|`ledger_entries.refund_id` 出现重复|增加唯一约束或幂等键|\n"
        "|取消订单存在成功支付|6|订单取消流程晚于支付回调|检查取消状态机和支付回调顺序|\n"
        "|退款挂订单但未挂 item|12|部分退款缺少 `item_id`，难以归因到商品|补齐 item 级退款明细|\n\n"
        "Agent 先自动探索四张表的字段和 join graph，再按 `order_id` 聚合到同一粒度做差异比较，最后继续按异常特征分桶归因。"
    )
    win.ask_tab.append_result(key, {
        "status": "completed",
        "answer_markdown": answer,
        "selected_sql": sql,
        "trace": _developer_consistency_trace(),
        "workflow_id": "wf_promo_consistency_audit",
    })
    _expand_latest_trace(app, win, key)
    view = win.ask_tab.view(key)
    if view is not None:
        return _grab_scrolled(app, view, win, "09-developer-consistency-audit", 0.0)
    return _grab(app, win, "09-developer-consistency-audit")


def show_clarification(app: QApplication, win: MainWindow) -> Path:
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
    return _grab(app, win.ask_tab, "05-clarification")


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
            "3. `03-chart-answer-analysis.png`",
            "   业务问题直接给出结论、证据和图表：净收入、退款率、渠道表现一屏可读。",
            "",
            "4. `04-chart-answer-breakdown.png`",
            "   回答不是纯文本：渠道拆解、库存风险和后续建议可以连续展示，适合业务复盘。",
            "",
            "5. `05-clarification.png`",
            "   当口径不唯一时先澄清：避免 AI 擅自假设财务归属、取消订单和差异阈值。",
            "",
            "6. `06-database-client-sql.png`",
            "   内置数据库客户端：多标签 SQL 编辑、结果表格、导出、历史和结构树在同一界面，SQL 证据可继续复核。",
            "",
            "7. `07-database-client-table.png`",
            "   表数据浏览与结构查看一体化：适合开发排障，也适合业务同学快速核对明细。",
            "",
            "8. `08-developer-field-exploration.png`",
            "   开发者专项：当字段名不存在时，Agent 会先查字段、读表结构、验证关联路径，再自动改写成可执行 SQL。",
            "",
            "9. `09-developer-consistency-audit.png`",
            "   开发者专项：跨 orders/payments/refunds/ledger_entries 自动对账，继续探索异常分桶和根因，而不是停在单条 SQL。",
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
    set_language("zh")
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(app_style())
    win, _service = _build_window(app)
    paths: list[Path] = []
    paths.append(show_assets_initializing(app, win))
    paths.append(show_runtime_thinking(app, win))
    paths.extend(show_chart_answer(app, win))
    paths.append(show_clarification(app, win))
    paths.extend(show_database_client(app, win))
    paths.append(show_developer_field_exploration(app, win))
    paths.append(show_developer_consistency_audit(app, win))
    copy = write_copy(paths)
    print(f"promo screenshots -> {OUT}")
    print(f"copy -> {copy}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
