from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import FORM_INNER_LABEL_RULES, configure_form, form_label
from dbaide.desktop.theme import Theme


class ConnectionForm(QWidget):
    """Reusable connection editor used by Settings and standalone dialogs."""

    def __init__(self, parent=None, *, conn_type: str = "sqlite") -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("connFormInner")
        inner.setStyleSheet(
            f"QWidget#connFormInner {{ background: {Theme.BG}; }}{FORM_INNER_LABEL_RULES}"
        )
        form = QFormLayout(inner)
        configure_form(form)

        self.name = QLineEdit()
        self.name.setFixedHeight(34)
        self.type_select = QComboBox()
        self.type_select.setFixedHeight(34)
        self.type_select.addItems(["sqlite", "mysql", "postgres"])
        self.type_select.setCurrentText(conn_type)
        self.path = QLineEdit()
        self.path.setFixedHeight(34)
        self.host = QLineEdit("localhost")
        self.host.setFixedHeight(34)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(3306)
        self.port.setFixedHeight(34)
        self.port.setMaximumWidth(120)
        self.database = QLineEdit()
        self.database.setFixedHeight(34)
        self.user = QLineEdit()
        self.user.setFixedHeight(34)
        self.password = QLineEdit()
        self.password.setFixedHeight(34)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)

        browse = compact_button("Browse…", width=88)
        browse.clicked.connect(self._browse)
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(8)
        path_layout.addWidget(self.path, 1)
        path_layout.addWidget(browse)

        form.addRow(form_label("Name"), self.name)
        form.addRow(form_label("Type"), self.type_select)
        form.addRow(form_label("SQLite path"), path_row)
        form.addRow(form_label("Host"), self.host)
        form.addRow(form_label("Port"), self.port)
        form.addRow(form_label("Database"), self.database)
        form.addRow(form_label("User"), self.user)
        form.addRow(form_label("Password"), self.password)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self.type_select.currentTextChanged.connect(self._on_type_changed)
        self._sync_fields(self.type_select.currentText(), reset_port=True)

    def _on_type_changed(self, conn_type: str) -> None:
        self._sync_fields(conn_type, reset_port=True)

    def load(self, payload: dict | None = None) -> None:
        payload = payload or {}
        self.name.setText(str(payload.get("name") or ""))
        self.type_select.blockSignals(True)
        self.type_select.setCurrentText(str(payload.get("type") or "sqlite"))
        self.type_select.blockSignals(False)
        self.path.setText(str(payload.get("path") or ""))
        self.host.setText(str(payload.get("host") or "localhost"))
        port = payload.get("port")
        if port not in (None, ""):
            self.port.setValue(int(port))
        self.database.setText(str(payload.get("database") or ""))
        self.user.setText(str(payload.get("user") or ""))
        self.password.clear()
        self._sync_fields(self.type_select.currentText(), reset_port=port in (None, ""))

    def clear(self, *, conn_type: str = "sqlite") -> None:
        self.name.clear()
        self.type_select.setCurrentText(conn_type)
        self.path.clear()
        self.host.setText("localhost")
        self.database.clear()
        self.user.clear()
        self.password.clear()
        self._sync_fields(conn_type, reset_port=True)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select SQLite database")
        if path:
            self.path.setText(path)
            if not self.name.text().strip():
                self.name.setText(path.rsplit("/", 1)[-1].split(".")[0])

    def _sync_fields(self, conn_type: str, *, reset_port: bool = False) -> None:
        sqlite = conn_type == "sqlite"
        self.path.setEnabled(sqlite)
        for widget in (self.host, self.port, self.database, self.user, self.password):
            widget.setEnabled(not sqlite)
        if reset_port:
            if conn_type in {"mysql", "mariadb"}:
                self.port.setValue(3306)
            elif conn_type in {"postgres", "postgresql"}:
                self.port.setValue(5432)

    def payload(self, *, make_default: bool = False) -> dict:
        return {
            "name": self.name.text().strip(),
            "type": self.type_select.currentText().strip(),
            "path": self.path.text().strip(),
            "host": self.host.text().strip(),
            "port": self.port.value(),
            "database": self.database.text().strip(),
            "user": self.user.text().strip(),
            "password": self.password.text(),
            "make_default": make_default,
        }

    def is_valid(self) -> bool:
        return bool(self.name.text().strip())


from PyQt6.QtWidgets import QDialog, QDialogButtonBox  # noqa: E402


class ConnectionDialog(QDialog):
    def __init__(self, parent=None, *, conn_type: str = "sqlite") -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Connection")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        self.form = ConnectionForm(conn_type=conn_type)
        layout.addWidget(self.form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self) -> dict:
        return self.form.payload(make_default=True)
