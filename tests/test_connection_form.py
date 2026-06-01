import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from dbaide.desktop.dialogs.connection import ConnectionForm


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_connection_form_load_preserves_custom_mysql_port(qapp):
    form = ConnectionForm(conn_type="mysql")
    form.load(
        {
            "name": "prod",
            "type": "mysql",
            "host": "db.example.com",
            "port": 3308,
            "database": "app",
            "user": "root",
        }
    )
    assert form.port.value() == 3308


def test_connection_form_type_change_resets_default_port(qapp):
    form = ConnectionForm(conn_type="sqlite")
    form.type_select.setCurrentText("mysql")
    assert form.port.value() == 3306
    form.port.setValue(3308)
    form.type_select.setCurrentText("postgres")
    assert form.port.value() == 5432
