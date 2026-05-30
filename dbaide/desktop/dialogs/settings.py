from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import configure_form
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.dialogs.connection import ConnectionForm
from dbaide.desktop.theme import APP_STYLE, Theme


class _SectionCard(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"""
            QFrame {{
                background: {Theme.SURFACE};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 12px;
            }}
            """
        )


class ModelForm(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("modelFormInner")
        inner.setStyleSheet(f"QWidget#modelFormInner {{ background: {Theme.BG}; }}")
        form = QFormLayout(inner)
        configure_form(form)
        self.profile_name = QLineEdit()
        self.profile_name.setFixedHeight(34)
        self.provider = QComboBox()
        self.provider.setFixedHeight(34)
        self.provider.addItems(["none", "openai_compatible"])
        self.base_url = QLineEdit()
        self.base_url.setFixedHeight(34)
        self.api_key = QLineEdit()
        self.api_key.setFixedHeight(34)
        self.api_key.setPlaceholderText("Required for new profiles; leave blank to keep saved key")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_id = QLineEdit()
        self.model_id.setFixedHeight(34)
        self.timeout = QSpinBox()
        self.timeout.setFixedHeight(34)
        self.timeout.setMaximumWidth(120)
        self.timeout.setRange(5, 600)
        self.timeout.setValue(60)
        form.addRow("Profile", self.profile_name)
        form.addRow("Provider", self.provider)
        form.addRow("Base URL", self.base_url)
        form.addRow("API Key", self.api_key)
        form.addRow("Model ID", self.model_id)
        form.addRow("Timeout (s)", self.timeout)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def load(self, payload: dict | None = None) -> None:
        payload = payload or {}
        self.profile_name.setText(str(payload.get("name") or "default"))
        self.provider.setCurrentText(str(payload.get("provider") or "none"))
        self.base_url.setText(str(payload.get("base_url") or ""))
        self.model_id.setText(str(payload.get("model") or ""))
        self.timeout.setValue(int(payload.get("timeout_seconds") or 60))
        self.api_key.clear()

    def clear(self) -> None:
        self.profile_name.setText("default")
        self.provider.setCurrentText("none")
        self.base_url.clear()
        self.api_key.clear()
        self.model_id.clear()
        self.timeout.setValue(60)

    def payload(self, *, make_default: bool = False) -> dict:
        payload = {
            "name": self.profile_name.text().strip() or "default",
            "provider": self.provider.currentText(),
            "base_url": self.base_url.text().strip(),
            "model": self.model_id.text().strip(),
            "timeout_seconds": self.timeout.value(),
            "make_default": make_default,
        }
        if self.api_key.text().strip():
            payload["api_key"] = self.api_key.text().strip()
        return payload


class SettingsDialog(QDialog):
    connection_saved = pyqtSignal(dict)
    connection_deleted = pyqtSignal(str)
    connection_test = pyqtSignal(dict)
    model_saved = pyqtSignal(dict)
    model_deleted = pyqtSignal(str)
    model_test = pyqtSignal(dict)

    def __init__(
        self,
        *,
        connections: list[dict],
        models: list[dict],
        default_connection: str = "",
        default_model: str = "",
        parent=None,
        initial_page: str = "connections",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(760, 540)
        self.resize(800, 580)
        self.setStyleSheet(APP_STYLE)
        self._connections = {c["name"]: dict(c) for c in connections}
        self._models = {m["name"]: dict(m) for m in models}
        self._default_connection = default_connection
        self._default_model = default_model
        self._selected_conn = ""
        self._selected_model = ""

        root = QHBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        nav_wrap = QWidget()
        nav_wrap.setFixedWidth(168)
        nav_wrap.setStyleSheet(f"background: {Theme.PANEL}; border-right: 1px solid {Theme.BORDER_SOFT};")
        nav_layout = QVBoxLayout(nav_wrap)
        nav_layout.setContentsMargins(0, 12, 0, 12)
        nav_layout.setSpacing(4)
        back = compact_button("← Back", width=120)
        back.clicked.connect(self.accept)
        nav_layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignHCenter)
        nav_layout.addSpacing(8)
        self.nav = QListWidget()
        self.nav.setStyleSheet(
            f"""
            QListWidget {{ background: transparent; border: none; }}
            QListWidget::item {{ padding: 12px 18px; }}
            QListWidget::item:selected {{ background: {Theme.PANEL_3}; color: {Theme.TEXT}; border-radius: 8px; }}
            """
        )
        for label, key in (("Connections", "connections"), ("Models", "models")):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav)
        nav_layout.addWidget(self.nav, 1)
        root.addWidget(nav_wrap)

        body = QVBoxLayout()
        body.setContentsMargins(24, 24, 24, 20)
        body.setSpacing(16)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_connections_page())
        self.stack.addWidget(self._build_models_page())
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        page_map = {"connections": 0, "models": 1, "model": 1}
        self.nav.setCurrentRow(page_map.get(initial_page, 0))
        self._reload_connection_list()
        self._reload_model_list()

    def _build_connections_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._page_header("Connections", "Manage database connections."))
        card = _SectionCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.conn_list = self._list_widget()
        self.conn_list.currentItemChanged.connect(self._on_connection_selected)
        row.addWidget(self.conn_list)
        form_col = QVBoxLayout()
        self.conn_form = ConnectionForm()
        form_col.addWidget(self.conn_form, 1)
        form_col.addLayout(self._conn_actions())
        row.addLayout(form_col, 1)
        card_layout.addLayout(row)
        layout.addWidget(card, 1)
        return page

    def _build_models_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._page_header("Models", "Configure LLM providers. Switch models from the composer."))
        card = _SectionCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.model_list = self._list_widget()
        self.model_list.currentItemChanged.connect(self._on_model_selected)
        row.addWidget(self.model_list)
        form_col = QVBoxLayout()
        self.model_form = ModelForm()
        form_col.addWidget(self.model_form, 1)
        form_col.addLayout(self._model_actions())
        row.addLayout(form_col, 1)
        card_layout.addLayout(row)
        layout.addWidget(card, 1)
        return page

    def _page_header(self, title: str, subtitle: str) -> QWidget:
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        h = QLabel(title)
        h.setStyleSheet("font-size:22px;font-weight:800;")
        s = QLabel(subtitle)
        s.setStyleSheet(f"color:{Theme.MUTED}; font-size:13px;")
        s.setWordWrap(True)
        col.addWidget(h)
        col.addWidget(s)
        return wrap

    def _list_widget(self) -> QListWidget:
        widget = QListWidget()
        widget.setMinimumWidth(180)
        widget.setMaximumWidth(220)
        widget.setStyleSheet(
            f"""
            QListWidget {{
                background: {Theme.PANEL};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 10px;
            }}
            QListWidget::item {{ padding: 10px 12px; }}
            QListWidget::item:selected {{ background: {Theme.PANEL_3}; }}
            """
        )
        return widget

    def _conn_actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.add_conn_btn = compact_button("Add", width=72)
        self.add_conn_btn.clicked.connect(self._add_connection)
        self.save_conn_btn = compact_button("Save", primary=True, width=80)
        self.save_conn_btn.clicked.connect(self._save_connection)
        self.test_conn_btn = compact_button("Test", width=72)
        self.test_conn_btn.clicked.connect(self._test_connection)
        self.conn_more = MenuButton("More ▾", max_width=88)
        self.conn_more.add_action("Set as default", self._set_default_connection)
        self.conn_more.add_action("Remove", self._remove_connection)
        actions.addWidget(self.add_conn_btn)
        actions.addStretch(1)
        actions.addWidget(self.save_conn_btn)
        actions.addWidget(self.test_conn_btn)
        actions.addWidget(self.conn_more)
        return actions

    def _model_actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.add_model_btn = compact_button("Add", width=72)
        self.add_model_btn.clicked.connect(self._add_model)
        self.save_model_btn = compact_button("Save", primary=True, width=80)
        self.save_model_btn.clicked.connect(self._save_model)
        self.test_model_btn = compact_button("Test", width=72)
        self.test_model_btn.clicked.connect(self._test_model)
        self.model_more = MenuButton("More ▾", max_width=88)
        self.model_more.add_action("Set as default", self._set_default_model)
        self.model_more.add_action("Remove", self._remove_model)
        actions.addWidget(self.add_model_btn)
        actions.addStretch(1)
        actions.addWidget(self.save_model_btn)
        actions.addWidget(self.test_model_btn)
        actions.addWidget(self.model_more)
        return actions

    def _on_nav(self, row: int) -> None:
        if row >= 0:
            self.stack.setCurrentIndex(row)

    def _reload_connection_list(self) -> None:
        self._fill_list(self.conn_list, self._connections, self._default_connection, self._selected_conn)
        if not self._connections:
            self.conn_form.clear()

    def _reload_model_list(self) -> None:
        self._fill_list(
            self.model_list,
            self._models,
            self._default_model,
            self._selected_model,
            label_fn=self._model_list_label,
        )
        if not self._models:
            self.model_form.clear()

    def _model_list_label(self, name: str, entry: dict) -> str:
        model_id = str(entry.get("model") or "").strip()
        return f"{name} · {model_id}" if model_id else name

    def _fill_list(self, widget, items, default, selected, *, label_fn=None) -> None:
        widget.blockSignals(True)
        widget.clear()
        for name in sorted(items):
            entry = items[name]
            label = label_fn(name, entry) if label_fn else f"{name} · {entry.get('type', '?')}"
            if name == default:
                label += " ★"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            widget.addItem(item)
        widget.blockSignals(False)
        if items:
            target = selected or default
            for i in range(widget.count()):
                if widget.item(i).data(Qt.ItemDataRole.UserRole) == target:
                    widget.setCurrentRow(i)
                    return
            widget.setCurrentRow(0)

    def _on_connection_selected(self, current, _previous) -> None:
        if not current:
            return
        name = str(current.data(Qt.ItemDataRole.UserRole) or "")
        self._selected_conn = name
        self.conn_form.load(self._connections.get(name))

    def _on_model_selected(self, current, _previous) -> None:
        if not current:
            return
        name = str(current.data(Qt.ItemDataRole.UserRole) or "")
        self._selected_model = name
        self.model_form.load(self._models.get(name))

    def _add_connection(self) -> None:
        self._selected_conn = ""
        self.conn_list.clearSelection()
        self.conn_form.clear()

    def _save_connection(self) -> None:
        payload = self.conn_form.payload(make_default=not self._connections)
        if not payload["name"]:
            QMessageBox.warning(self, "Settings", "Connection name is required.")
            return
        self.connection_saved.emit(payload)
        self._connections[payload["name"]] = payload
        self._selected_conn = payload["name"]
        if payload.get("make_default"):
            self._default_connection = payload["name"]
        self._reload_connection_list()

    def _test_connection(self) -> None:
        payload = self.conn_form.payload()
        if not payload["name"]:
            QMessageBox.warning(self, "Settings", "Select or enter a connection to test.")
            return
        self.connection_test.emit(payload)

    def _set_default_connection(self) -> None:
        name = self.conn_form.payload()["name"]
        if not name or name not in self._connections:
            QMessageBox.warning(self, "Settings", "Save the connection first.")
            return
        self._default_connection = name
        payload = dict(self._connections[name])
        payload["make_default"] = True
        self.connection_saved.emit(payload)
        self._reload_connection_list()

    def _remove_connection(self) -> None:
        name = self.conn_form.payload()["name"]
        if not name or name not in self._connections:
            return
        if QMessageBox.question(self, "Settings", f"Remove connection '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        self.connection_deleted.emit(name)
        self._connections.pop(name, None)
        if self._default_connection == name:
            self._default_connection = next(iter(self._connections), "")
        self._selected_conn = ""
        self._reload_connection_list()

    def _add_model(self) -> None:
        self._selected_model = ""
        self.model_list.clearSelection()
        self.model_form.clear()

    def _save_model(self) -> None:
        payload = self.model_form.payload(make_default=not self._models)
        if not payload["name"]:
            QMessageBox.warning(self, "Settings", "Profile name is required.")
            return
        self.model_saved.emit(payload)
        self._models[payload["name"]] = payload
        self._selected_model = payload["name"]
        if payload.get("make_default"):
            self._default_model = payload["name"]
        self._reload_model_list()

    def _test_model(self) -> None:
        payload = self.model_form.payload()
        if not payload.get("name"):
            QMessageBox.warning(self, "Settings", "Select or enter a model profile to test.")
            return
        self.model_test.emit(payload)

    def _set_default_model(self) -> None:
        name = self.model_form.payload()["name"]
        if not name or name not in self._models:
            QMessageBox.warning(self, "Settings", "Save the model profile first.")
            return
        self._default_model = name
        payload = dict(self._models[name])
        payload["make_default"] = True
        self.model_saved.emit(payload)
        self._reload_model_list()

    def _remove_model(self) -> None:
        name = self.model_form.payload()["name"]
        if not name or name not in self._models:
            return
        if QMessageBox.question(self, "Settings", f"Remove model profile '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        self.model_deleted.emit(name)
        self._models.pop(name, None)
        if self._default_model == name:
            self._default_model = next(iter(self._models), "")
        self._selected_model = ""
        self._reload_model_list()

    def show_test_result(self, ok: bool, message: str) -> None:
        if ok:
            QMessageBox.information(self, "Settings", message)
        else:
            QMessageBox.warning(self, "Settings", message)
