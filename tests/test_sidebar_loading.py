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
    db_actions = sidebar.tree.itemWidget(db_item, 1)
    db_buttons = db_actions.findChildren(QToolButton)
    assert len(db_buttons) == 2
    assert db_buttons[0].toolTip() == "View doc"
    assert [a.text() for a in db_buttons[1].menu().actions()] == ["Edit note", "Update from database"]

    col_item = db_item.child(0).child(0)
    col_actions = sidebar.tree.itemWidget(col_item, 1)
    col_buttons = col_actions.findChildren(QToolButton)
    assert len(col_buttons) == 1
    assert [a.text() for a in col_buttons[0].menu().actions()] == ["Edit note"]
