from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
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
from dbaide.desktop.components.inputs import FORM_INNER_LABEL_RULES, Combo, configure_form, form_label
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
        self.name.setFixedHeight(26)
        self.type_select = Combo()
        self.type_select.setFixedHeight(26)
        self.type_select.addItems(["sqlite", "mysql", "postgres"])
        self.type_select.setCurrentText(conn_type)
        self.path = QLineEdit()
        self.path.setFixedHeight(26)
        self.host = QLineEdit("localhost")
        self.host.setFixedHeight(26)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(3306)
        self.port.setFixedHeight(26)
        self.port.setMaximumWidth(120)
        self.database = QLineEdit()
        self.database.setFixedHeight(26)
        self.user = QLineEdit()
        self.user.setFixedHeight(26)
        self.password = QLineEdit()
        self.password.setFixedHeight(26)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.load_profile = Combo()
        self.load_profile.setFixedHeight(26)
        self.load_profile.addItems(["production", "staging", "dev"])
        self.load_profile.setToolTip(
            "production: lowest DB load (light profiling, low concurrency, strict limits).\n"
            "staging: balanced. dev: highest concurrency and limits."
        )
        self.session_timezone = QLineEdit("UTC")
        self.session_timezone.setFixedHeight(26)
        self.session_timezone.setPlaceholderText("UTC, +00:00, +08:00")
        self.session_timezone.setToolTip(
            "Session time zone applied after connecting. MySQL accepts offsets like +00:00; "
            "PostgreSQL also accepts names like UTC."
        )

        from dbaide.i18n import t
        browse = compact_button(t("conn.browse"), width=88)
        browse.clicked.connect(self._browse)
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(8)
        path_layout.addWidget(self.path, 1)
        path_layout.addWidget(browse)

        self._form = form
        form.addRow(form_label(t("conn.name")), self.name)
        form.addRow(form_label(t("conn.type")), self.type_select)
        form.addRow(form_label(t("conn.sqlite_path")), path_row)
        self._row_path = 2
        form.addRow(form_label(t("conn.host")), self.host)
        form.addRow(form_label(t("conn.port")), self.port)
        form.addRow(form_label(t("conn.database")), self.database)
        form.addRow(form_label(t("conn.user")), self.user)
        form.addRow(form_label(t("conn.password")), self.password)
        form.addRow(form_label(t("conn.session_timezone")), self.session_timezone)
        self._rows_server = (3, 4, 5, 6, 7, 8)  # host, port, database, user, password, timezone
        form.addRow(form_label(t("conn.load_profile")), self.load_profile)
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
            try:
                self.port.setValue(int(port))
            except (TypeError, ValueError):
                pass
        self.database.setText(str(payload.get("database") or ""))
        self.user.setText(str(payload.get("user") or ""))
        self.password.clear()
        self.session_timezone.setText(str(payload.get("session_timezone") or "UTC"))
        self.load_profile.setCurrentText(str(payload.get("load_profile") or "production"))
        self._sync_fields(self.type_select.currentText(), reset_port=port in (None, ""))

    def clear(self, *, conn_type: str = "sqlite") -> None:
        self.name.clear()
        self.type_select.setCurrentText(conn_type)
        self.path.clear()
        self.host.setText("localhost")
        self.database.clear()
        self.user.clear()
        self.password.clear()
        self.session_timezone.setText("UTC")
        self.load_profile.setCurrentText("production")
        self._sync_fields(conn_type, reset_port=True)

    def _set_row_visible(self, row: int, visible: bool) -> None:
        # Qt 6.4+ has setRowVisible; fall back to toggling both row widgets.
        try:
            self._form.setRowVisible(row, visible)
        except (AttributeError, TypeError):
            from PyQt6.QtWidgets import QFormLayout
            for role in (QFormLayout.ItemRole.LabelRole, QFormLayout.ItemRole.FieldRole):
                item = self._form.itemAt(row, role)
                if item is not None and item.widget() is not None:
                    item.widget().setVisible(visible)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select SQLite database")
        if path:
            self.path.setText(path)
            if not self.name.text().strip():
                self.name.setText(path.rsplit("/", 1)[-1].split(".")[0])

    def _sync_fields(self, conn_type: str, *, reset_port: bool = False) -> None:
        # Show only the fields relevant to the type: SQLite → just the file path;
        # server types → host/port/database/user/password. Irrelevant rows hide
        # entirely (cleaner than greying out).
        sqlite = conn_type == "sqlite"
        self._set_row_visible(self._row_path, sqlite)
        for row in self._rows_server:
            self._set_row_visible(row, not sqlite)
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
            "session_timezone": self.session_timezone.text().strip() or "UTC",
            "load_profile": self.load_profile.currentText().strip(),
            "make_default": make_default,
        }

    def is_valid(self) -> bool:
        return bool(self.name.text().strip())


from PyQt6.QtWidgets import QDialog  # noqa: E402


class ConnectionDialog(QDialog):
    def __init__(self, parent=None, *, conn_type: str = "sqlite") -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        self.setWindowTitle(t("conn.add_title"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        self.form = ConnectionForm(conn_type=conn_type)
        layout.addWidget(self.form)
        # Match the app's button language (accent primary + ghost) instead of a
        # native QDialogButtonBox, so dialogs read consistently.
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 6, 0, 0)
        btn_row.addStretch(1)
        cancel_btn = compact_button(t("btn.cancel"), width=88)
        save_btn = compact_button(t("btn.save"), primary=True, width=88)
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def payload(self) -> dict:
        return self.form.payload(make_default=True)
