import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QToolButton

from dbaide.desktop.views.sidebar import Sidebar


def _app():
    return QApplication.instance() or QApplication([])


def test_sidebar_schema_loading_spinner_stops_after_load():
    app = _app()
    sidebar = Sidebar()

    sidebar.set_loading("Reading schema")
    assert sidebar.tree.topLevelItemCount() == 1
    assert sidebar.tree.topLevelItem(0).text(0) == "Reading schema"
    assert sidebar._schema_busy.active is True

    sidebar.update_loading("Reading schema · main")
    assert sidebar.tree.topLevelItem(0).text(0) == "Reading schema · main"

    sidebar.load_schema([])
    app.processEvents()
    assert sidebar._schema_busy.active is False
    assert sidebar._schema_loading_item is None


def test_sidebar_schema_row_actions_use_more_menu():
    app = _app()
    sidebar = Sidebar()
    sidebar.load_schema([
        {
            "kind": "database",
            "name": "main",
            "path": "shop.main",
            "children": [
                {
                    "kind": "table",
                    "name": "orders",
                    "path": "shop.main.orders",
                    "column_count": 1,
                    "children": [
                        {
                            "kind": "column",
                            "name": "id",
                            "path": "shop.main.orders.id",
                            "data_type": "INTEGER",
                        }
                    ],
                }
            ],
        }
    ])
    app.processEvents()

    db_item = sidebar.tree.topLevelItem(0)
    assert db_item.isExpanded() is False
    db_actions = sidebar.tree.itemWidget(db_item, 1)
    db_buttons = db_actions.findChildren(QToolButton)
    assert len(db_buttons) == 1
    db_menu_texts = [a.text() for a in db_buttons[0].menu().actions()]
    assert "View doc" in db_menu_texts
    assert "Edit note" in db_menu_texts
    assert "Update from database" in db_menu_texts
    assert "Backup database…" in db_menu_texts
    assert "Copy name" in db_menu_texts

    sidebar.set_node_refreshing(db_item.data(0, Qt.ItemDataRole.UserRole), True)
    app.processEvents()
    busy_actions = sidebar.tree.itemWidget(db_item, 1)
    busy_buttons = busy_actions.findChildren(QToolButton)
    assert len(busy_buttons) == 1
    assert busy_buttons[0].isEnabled() is False
    assert busy_buttons[0].menu() is None
    assert sidebar._node_busy.active is True

    sidebar.set_node_refreshing(db_item.data(0, Qt.ItemDataRole.UserRole), False)
    app.processEvents()
    restored_actions = sidebar.tree.itemWidget(db_item, 1)
    restored_buttons = restored_actions.findChildren(QToolButton)
    assert restored_buttons[0].isEnabled() is True
    restored_texts = [a.text() for a in restored_buttons[0].menu().actions()]
    assert "Edit note" in restored_texts
    assert "Backup database…" in restored_texts
    assert sidebar._node_busy.active is False

    col_item = db_item.child(0).child(0)
    col_actions = sidebar.tree.itemWidget(col_item, 1)
    col_buttons = col_actions.findChildren(QToolButton)
    assert len(col_buttons) == 1
    col_menu_texts = [a.text() for a in col_buttons[0].menu().actions()]
    assert "Edit note" in col_menu_texts
    assert "Copy name" in col_menu_texts


def test_sidebar_schema_tree_preserves_expansion_on_refresh():
    app = _app()
    sidebar = Sidebar()
    rows = [
        {
            "kind": "database",
            "name": "main",
            "path": "shop.main",
            "children": [
                {
                    "kind": "table",
                    "name": "orders",
                    "path": "shop.main.orders",
                    "column_count": 1,
                    "children": [
                        {
                            "kind": "column",
                            "name": "id",
                            "path": "shop.main.orders.id",
                            "data_type": "INTEGER",
                        }
                    ],
                }
            ],
        }
    ]
    sidebar.load_schema(rows)
    app.processEvents()

    db_item = sidebar.tree.topLevelItem(0)
    table_item = db_item.child(0)
    assert db_item.isExpanded() is False
    db_item.setExpanded(True)
    table_item.setExpanded(True)
    app.processEvents()

    sidebar.load_schema(rows)
    app.processEvents()

    db_item = sidebar.tree.topLevelItem(0)
    table_item = db_item.child(0)
    assert db_item.isExpanded() is True
    assert table_item.isExpanded() is True


def test_sidebar_schema_tree_stays_collapsed_by_default_during_build_progress():
    app = _app()
    sidebar = Sidebar()
    sidebar.load_schema([
        {
            "kind": "database",
            "name": "main",
            "path": "shop.main",
            "children": [
                {
                    "kind": "table",
                    "name": "orders",
                    "path": "shop.main.orders",
                    "column_count": 0,
                    "children": [],
                }
            ],
        }
    ])
    app.processEvents()
    sidebar.start_build_progress("Building")
    sidebar.load_schema([
        {
            "kind": "database",
            "name": "main",
            "path": "shop.main",
            "children": [
                {
                    "kind": "table",
                    "name": "orders",
                    "path": "shop.main.orders",
                    "column_count": 1,
                    "children": [],
                },
                {
                    "kind": "table",
                    "name": "users",
                    "path": "shop.main.users",
                    "column_count": 2,
                    "children": [],
                },
            ],
        }
    ])
    app.processEvents()
    assert sidebar.tree.topLevelItem(0).isExpanded() is False


def test_sidebar_incremental_schema_sync_during_build():
    app = _app()
    sidebar = Sidebar()
    rows_v1 = [
        {
            "kind": "database",
            "name": "main",
            "path": "shop.main",
            "children": [
                {
                    "kind": "table",
                    "name": "orders",
                    "path": "shop.main.orders",
                    "column_count": 1,
                    "children": [],
                }
            ],
        }
    ]
    sidebar.load_schema(rows_v1)
    app.processEvents()
    orders = sidebar.tree.topLevelItem(0).child(0)
    assert sidebar.tree.itemWidget(orders, 1) is not None

    sidebar.start_build_progress("Building")
    rows_v2 = [
        {
            "kind": "database",
            "name": "main",
            "path": "shop.main",
            "children": [
                {
                    "kind": "table",
                    "name": "orders",
                    "path": "shop.main.orders",
                    "column_count": 2,
                    "children": [
                        {
                            "kind": "column",
                            "name": "id",
                            "path": "shop.main.orders.id",
                            "data_type": "INTEGER",
                        }
                    ],
                },
                {
                    "kind": "table",
                    "name": "users",
                    "path": "shop.main.users",
                    "column_count": 1,
                    "children": [],
                },
            ],
        }
    ]
    sidebar.load_schema(rows_v2)
    sidebar._flush_schema_render()
    app.processEvents()

    assert sidebar.tree.topLevelItemCount() == 1
    assert sidebar.tree.topLevelItem(0).childCount() == 2
    assert sidebar.tree.topLevelItem(0).child(0).text(0) == "orders (2)"
    assert sidebar.tree.itemWidget(sidebar.tree.topLevelItem(0).child(0), 1) is not None


def test_sidebar_asset_state_summary_is_persistent():
    app = _app()
    sidebar = Sidebar()
    sidebar.load_schema([
        {
            "kind": "database",
            "name": "main",
            "path": "shop.main",
            "asset_summary": {
                "state": "base",
                "tables": 1,
                "columns": 2,
                "sampled_tables": 0,
                "errors": 0,
            },
            "children": [
                {
                    "kind": "table",
                    "name": "orders",
                    "path": "shop.main.orders",
                    "column_count": 2,
                    "asset_state": "base",
                    "children": [],
                }
            ],
        }
    ])
    app.processEvents()
    assert not sidebar._asset_state.isHidden()
    assert sidebar._asset_state_title.text()
    assert "1" in sidebar._asset_state_detail.text()


def test_sidebar_asset_state_failure_stays_visible():
    app = _app()
    sidebar = Sidebar()
    sidebar.load_schema([], error="permission denied")
    app.processEvents()
    assert not sidebar._asset_state.isHidden()
    assert sidebar._asset_state_title.text()
    assert sidebar._asset_state_detail.text() == "permission denied"


def test_sidebar_build_progress_spinner_only_while_discovering():
    app = _app()
    from dbaide.desktop.views.sidebar import Sidebar

    sidebar = Sidebar()
    sidebar.start_build_progress("Reading schema")
    app.processEvents()
    assert sidebar._build_progress_busy.active is True
    assert sidebar._build_progress_count.text() == ""
    assert sidebar.tree.topLevelItemCount() == 0

    sidebar.update_build_progress({
        "node_id": "build:db:main",
        "completed_tables": 0,
        "total_tables": 3,
    })
    sidebar._flush_build_progress()
    app.processEvents()
    assert sidebar._build_progress_count.text() == "0/3"
    assert sidebar._build_progress_busy.active is True


def test_sidebar_skips_tree_loading_while_build_progress_active():
    app = _app()
    sidebar = Sidebar()
    sidebar.start_build_progress("Reading schema")
    sidebar.set_loading("should not appear")
    sidebar.update_loading("also ignored")
    app.processEvents()
    assert sidebar.tree.topLevelItemCount() == 0
    assert sidebar._schema_loading_item is None


def test_sidebar_reset_live_updates_drops_stale_schema_flush():
    app = _app()
    sidebar = Sidebar()
    sidebar.load_schema([
        {
            "kind": "database",
            "name": "main",
            "path": "shop.main",
            "children": [
                {
                    "kind": "table",
                    "name": "orders",
                    "path": "shop.main.orders",
                    "column_count": 1,
                    "children": [],
                }
            ],
        }
    ])
    app.processEvents()
    sidebar.start_build_progress("Building")
    sidebar.load_schema([
        {
            "kind": "database",
            "name": "main",
            "path": "shop.main",
            "children": [
                {
                    "kind": "table",
                    "name": "orders",
                    "path": "shop.main.orders",
                    "column_count": 9,
                    "children": [],
                }
            ],
        }
    ])
    assert sidebar._schema_render_pending is not None
    sidebar.reset_live_updates()
    sidebar._flush_schema_render()
    app.processEvents()
    assert sidebar.tree.topLevelItem(0).child(0).text(0) == "orders (1)"
    assert sidebar._build_progress.isHidden()


def test_sidebar_failed_build_progress_clears_active():
    app = _app()
    sidebar = Sidebar()
    sidebar.start_build_progress("Building")
    sidebar.finish_build_progress("boom", failed=True)
    assert sidebar._build_progress_active is True
    sidebar._hide_build_progress_if_current(sidebar._build_progress_token)
    assert sidebar._build_progress_active is False


def test_sidebar_filter_matches_database_name():
    """Filtering by a DATABASE name shows that whole database (all its tables), not an
    empty tree just because no table/column also contained the needle."""
    app = _app()
    sidebar = Sidebar()
    rows = [
        {"kind": "database", "name": "analytics", "path": "c.analytics", "children": [
            {"kind": "table", "name": "events", "path": "c.analytics.events", "column_count": 0, "children": []},
        ]},
        {"kind": "database", "name": "billing", "path": "c.billing", "children": [
            {"kind": "table", "name": "invoices", "path": "c.billing.invoices", "column_count": 0, "children": []},
        ]},
    ]
    sidebar._render(rows)
    sidebar._rows = rows
    sidebar._filter_tree("analytics")          # db-name match → whole db kept
    tree = sidebar.tree
    top = [tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(tree.topLevelItemCount())]
    names = [d.get("name") for d in top if isinstance(d, dict)]
    assert "analytics" in names and "billing" not in names
    # The matched database still shows its tables.
    assert tree.topLevelItem(0).childCount() == 1
