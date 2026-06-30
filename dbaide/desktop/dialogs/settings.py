from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressDialog,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dbaide.step_budget import MAX_AGENT_MAX_STEPS, MIN_AGENT_MAX_STEPS
from dbaide.desktop.dialogs.message_dialog import alert as dialog_alert, confirm as dialog_confirm, warn as dialog_warn
from dbaide.app_info import (
    APP_NAME,
    DEVELOPER_NAME,
    DEVELOPER_URL,
    LICENSE_NAME,
    app_version,
    project_links,
)
from dbaide.desktop.components.base import (
    ElidingLabel,
    button_icon_color,
    compact_button,
    ghost_action_button,
)
from dbaide.desktop.components.icon_button import IconToolButton
from dbaide.desktop.components.icons import more_icon, svg_icon
from dbaide.desktop.components.inputs import (
    Combo,
    FORM_INNER_LABEL_RULES,
    configure_compact_field,
    configure_form,
    form_label,
    STANDARD_FIELD_HEIGHT,
)
from dbaide.desktop.components.menu import MenuButton
from dbaide.desktop.dialogs.connection import ConnectionForm
from dbaide.desktop.dialogs.file_dialogs import get_open_file_name
from dbaide.desktop.theme import app_style, Theme
from dbaide.i18n import t as _pt


_NEW_CONNECTION_ID = "__new_connection__"
_NEW_MODEL_ID = "__new_model__"


class _SectionCard(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Flat container: a subtle background groups the section without adding a
        # heavy bordered/rounded box on top of the already-bordered inputs inside it.
        self.setStyleSheet(
            f"""
            QFrame {{
                background: {Theme.PANEL};
                border: none;
                border-radius: 8px;
            }}
            """
        )


class ModelForm(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("modelFormInner")
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner.setStyleSheet(
            f"QWidget#modelFormInner {{ background: transparent; }}{FORM_INNER_LABEL_RULES}"
        )
        form = QFormLayout(inner)
        configure_form(form)
        self.profile_name = QLineEdit()
        self.provider = Combo()
        self.provider.addItems(["none", "openai_compatible", "anthropic", "openai_responses"])
        self.base_url = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setPlaceholderText(_pt("settings.api_key_placeholder"))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_id = QLineEdit()
        self.timeout = QSpinBox()
        self.timeout.setMaximumWidth(120)
        self.timeout.setRange(5, 600)
        self.timeout.setValue(60)
        self.context_length = QSpinBox()
        self.context_length.setMaximumWidth(120)
        self.context_length.setRange(4, 2048)
        self.context_length.setValue(32)
        self.context_length.setSuffix(" k")
        for field in (self.profile_name, self.provider, self.base_url, self.api_key, self.model_id):
            configure_compact_field(field, height=STANDARD_FIELD_HEIGHT)
        configure_compact_field(self.timeout, height=STANDARD_FIELD_HEIGHT, max_width=120)
        configure_compact_field(self.context_length, height=STANDARD_FIELD_HEIGHT, max_width=120)
        form.addRow(form_label(t("model.profile")), self.profile_name)
        form.addRow(form_label(t("model.provider")), self.provider)
        form.addRow(form_label(t("model.base_url")), self.base_url)
        form.addRow(form_label(t("model.api_key")), self.api_key)
        form.addRow(form_label(t("model.model_id")), self.model_id)
        form.addRow(form_label(t("model.timeout")), self.timeout)
        form.addRow(form_label(t("model.context_length")), self.context_length)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def load(self, payload: dict | None = None) -> None:
        payload = payload or {}
        self.profile_name.setText(str(payload.get("name") or "default"))
        self.provider.setCurrentText(str(payload.get("provider") or "none"))
        self.base_url.setText(str(payload.get("base_url") or ""))
        self.model_id.setText(str(payload.get("model") or ""))
        self.timeout.setValue(int(payload.get("timeout_seconds") or 60))
        ctx_k = max(4, int(payload.get("context_length") or 32000) // 1000)
        self.context_length.setValue(ctx_k)
        self.api_key.clear()
        if payload.get("has_api_key"):
            self.api_key.setPlaceholderText(_pt("settings.api_key_saved"))
        else:
            self.api_key.setPlaceholderText(_pt("settings.api_key_placeholder"))

    def clear(self) -> None:
        self.profile_name.clear()
        self.provider.setCurrentText("openai_compatible")
        self.base_url.clear()
        self.api_key.clear()
        self.api_key.setPlaceholderText(_pt("settings.api_key_placeholder"))
        self.model_id.clear()
        self.timeout.setValue(60)
        self.context_length.setValue(32)

    def payload(self, *, make_default: bool = False) -> dict:
        payload = {
            "name": self.profile_name.text().strip(),
            "provider": self.provider.currentText(),
            "base_url": self.base_url.text().strip(),
            "model": self.model_id.text().strip(),
            "timeout_seconds": self.timeout.value(),
            "context_length": self.context_length.value() * 1000,
            "make_default": make_default,
        }
        if self.api_key.text().strip():
            payload["api_key"] = self.api_key.text().strip()
        return payload


class _WorkbookPanel(QWidget):
    """Right-pane manager shown when the selected connection is an Excel collection:
    lists its workbooks and lets the user add or remove them. Pure view — it emits
    intent signals; the dialog performs the filesystem work and reloads it."""

    add_requested = pyqtSignal()
    remove_requested = pyqtSignal(str)     # workbook id
    rename_requested = pyqtSignal(str)     # workbook id
    reimport_requested = pyqtSignal(str)   # workbook id
    preview_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        self._title = ElidingLabel("")
        self._title.setStyleSheet(f"color:{Theme.TEXT}; font-size:14px; font-weight:600;")
        self._hint = QLabel(_pt("excel.collection_hint"))
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
        outer.addWidget(self._title)
        outer.addWidget(self._hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._list_host = QWidget()
        self._list_host.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch(1)
        scroll.setWidget(self._list_host)
        outer.addWidget(scroll, 1)

        self._add_btn = compact_button(_pt("excel.add_workbook"), width=124)
        self._add_btn.clicked.connect(lambda: self.add_requested.emit())
        self._preview_btn = compact_button(_pt("excel.preview_btn"), width=110)
        self._preview_btn.clicked.connect(lambda: self.preview_requested.emit())
        add_row = QHBoxLayout()
        add_row.addWidget(self._add_btn)
        add_row.addWidget(self._preview_btn)
        add_row.addStretch(1)
        outer.addLayout(add_row)

    def load(self, name: str, workbooks: list) -> None:
        self._title.setText(_pt("excel.collection_title", name=name))
        while self._list_layout.count() > 1:          # keep the trailing stretch
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not workbooks:
            empty = QLabel(_pt("excel.empty"))
            empty.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
            self._list_layout.insertWidget(0, empty)
            return
        for i, wb in enumerate(workbooks):
            self._list_layout.insertWidget(i, self._row(wb))

    def _row(self, wb) -> QWidget:
        sheet_count = len(wb.sheets)
        row_count = sum(s.row_count for s in wb.sheets)
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background:{Theme.PANEL_2}; border:1px solid {Theme.BORDER_SOFT};"
            f" border-radius:8px; }}"
        )
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(8)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name = ElidingLabel(wb.name)      # logical name = the table the user renamed / queries
        name.setStyleSheet(
            f"color:{Theme.TEXT}; font-size:13px; font-weight:500; background:transparent; border:none;"
        )
        counts = _pt("excel.sheet_rows", sheets=sheet_count, rows=f"{row_count:,}")
        meta = ElidingLabel(f"{wb.source_filename} · {counts}")
        meta.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px; background:transparent; border:none;")
        text_col.addWidget(name)
        text_col.addWidget(meta)
        lay.addLayout(text_col, 1)
        reimport = ghost_action_button(_pt("excel.reimport"))
        reimport.clicked.connect(lambda _checked=False, wid=wb.id: self.reimport_requested.emit(wid))
        lay.addWidget(reimport)
        rename = ghost_action_button(_pt("excel.rename"))
        rename.clicked.connect(lambda _checked=False, wid=wb.id: self.rename_requested.emit(wid))
        lay.addWidget(rename)
        remove = ghost_action_button(_pt("excel.remove_workbook"))
        remove.setStyleSheet(
            remove.styleSheet().replace(
                "QPushButton:hover { background: " + Theme.PANEL_2 + "; color: " + Theme.TEXT + "; }",
                "QPushButton:hover { background: " + Theme.PANEL_3 + "; color: " + Theme.RED + "; }",
            )
        )
        remove.clicked.connect(lambda _checked=False, wid=wb.id: self.remove_requested.emit(wid))
        lay.addWidget(remove)
        return frame


from dbaide.desktop.window_chrome import ChromeDialog


class _OptionCard(QFrame):
    """A large clickable option (title + description) for the new-connection chooser."""

    clicked = pyqtSignal()

    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("optionCard")
        self.setStyleSheet(
            f"""
            QFrame#optionCard {{ background:{Theme.PANEL_2}; border:1px solid {Theme.BORDER_SOFT};
                                 border-radius:10px; }}
            QFrame#optionCard:hover {{ background:{Theme.PANEL_3}; border-color:{Theme.ACCENT}; }}
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(3)
        head = QLabel(title)
        head.setStyleSheet(f"color:{Theme.TEXT}; font-size:14px; font-weight:600; background:transparent; border:none;")
        desc = QLabel(description)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px; background:transparent; border:none;")
        lay.addWidget(head)
        lay.addWidget(desc)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _ConnectionKindDialog(ChromeDialog):
    """Asks whether a new connection is a database or an Excel/CSV collection."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self._kind = ""
        self.setWindowTitle(_pt("conn.kind_title"))
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(app_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)
        heading = QLabel(_pt("conn.kind_title"))
        heading.setStyleSheet(f"color:{Theme.TEXT}; font-size:16px; font-weight:700; background:transparent;")
        root.addWidget(heading)
        hint = QLabel(_pt("conn.kind_hint"))
        hint.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px; background:transparent;")
        root.addWidget(hint)

        db = _OptionCard(_pt("conn.kind.database"), _pt("conn.kind.database_desc"))
        db.clicked.connect(lambda: self._pick("database"))
        excel = _OptionCard(_pt("conn.kind.excel"), _pt("conn.kind.excel_desc"))
        excel.clicked.connect(lambda: self._pick("excel"))
        root.addWidget(db)
        root.addWidget(excel)

        cancel_row = QHBoxLayout()
        cancel_row.addStretch(1)
        cancel = compact_button(_pt("dialog.cancel"), width=88)
        cancel.clicked.connect(self.reject)
        cancel_row.addWidget(cancel)
        root.addLayout(cancel_row)

    def _pick(self, kind: str) -> None:
        self._kind = kind
        self.accept()

    def kind(self) -> str:
        return self._kind


def choose_connection_kind(parent) -> str:
    """Return "database", "excel", or "" if cancelled."""
    dialog = _ConnectionKindDialog(parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return ""
    return dialog.kind()


class _ImportWorker(QThread):
    """Runs an Excel import off the UI thread. The work callable receives a progress callback;
    it touches only files/sqlite (its own connection in this thread), never Qt — progress is
    relayed to the UI via a signal."""

    progress = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, work, parent=None) -> None:
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        try:
            result = self._work(lambda msg: self.progress.emit(str(msg)))
        except BaseException as exc:  # noqa: BLE001 - surfaced to the UI thread
            self.failed.emit(exc)
            return
        self.done.emit(result)


class SettingsDialog(ChromeDialog):
    connection_saved = pyqtSignal(dict)
    connection_deleted = pyqtSignal(str)
    excel_collection_changed = pyqtSignal(str)   # workbooks added/removed → re-sync schema
    connection_test = pyqtSignal(dict)
    model_saved = pyqtSignal(dict)
    model_deleted = pyqtSignal(str)
    model_test = pyqtSignal(dict)
    resource_saved = pyqtSignal(dict)
    language_changed = pyqtSignal(str)
    theme_changed = pyqtSignal(str)
    stream_answers_changed = pyqtSignal(bool)
    debug_trace_changed = pyqtSignal(bool)
    export_connection = pyqtSignal(str)       # connection name
    import_requested = pyqtSignal(str)        # file path
    export_all_requested = pyqtSignal()

    # Numeric resource knobs shown on the Resources page, grouped by category.
    # Each group: (i18n_group_key, [(field_key, min, max), ...])
    _RESOURCE_GROUPS = (
        ("res.group.database", (
            ("max_inflight_queries", 1, 64),
            ("statement_timeout_seconds", 1, 600),
            ("default_row_limit", 1, 100000),
            ("max_row_limit", 1, 1000000),
            ("big_table_rows", 1000, 1000000000),
            ("explain_max_rows", 1000, 1000000000),
            ("optimize_advise_rows", 0, 1000000000),
            ("join_sample_size", 10, 1000),
        )),
        ("res.group.agent", (
            ("agent_max_steps", MIN_AGENT_MAX_STEPS, MAX_AGENT_MAX_STEPS),
            ("prior_turns_window", 0, 20),
            ("max_batch_tools", 1, 16),
            ("latest_result_limit", 0, 20000),
            ("session_uncompressed_turns", 0, 10),
            ("compress_threshold", 50, 95),
        )),
        ("res.group.build", (
            ("build_max_workers", 1, 32),
        )),
    )

    def __init__(
        self,
        *,
        connections: list[dict],
        models: list[dict],
        default_connection: str = "",
        default_model: str = "",
        resource_defaults: dict | None = None,
        language: str = "en",
        stream_answers: bool = True,
        debug_trace: bool = False,
        config_dir: str | Path = "",
        parent=None,
        initial_page: str = "connections",
    ) -> None:
        super().__init__(parent)
        from dbaide.i18n import t as _t
        self._config_dir = Path(config_dir) if config_dir else None
        self._language = language
        self._stream_answers = bool(stream_answers)
        self._debug_trace = bool(debug_trace)
        from dbaide.desktop.theme import current_theme_name
        self._theme = current_theme_name()
        self.setWindowTitle(_t("settings.title"))
        self.setMinimumSize(760, 540)
        self.resize(800, 580)
        self.setStyleSheet(app_style())
        self._connections = {c["name"]: dict(c) for c in connections}
        self._models = {m["name"]: dict(m) for m in models}
        self._default_connection = default_connection
        self._default_model = default_model
        self._selected_conn = ""
        self._selected_model = ""
        rd = resource_defaults or {}
        self._resource_values = dict(rd.get("values") or {})
        self._resource_presets = dict(rd.get("presets") or {})

        root = QHBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        nav_wrap = QWidget()
        nav_wrap.setFixedWidth(168)
        nav_wrap.setStyleSheet(f"background: {Theme.PANEL}; border-right: 1px solid {Theme.BORDER_SOFT};")
        nav_layout = QVBoxLayout(nav_wrap)
        nav_layout.setContentsMargins(8, 12, 8, 12)
        nav_layout.setSpacing(4)
        # Compact, flat back action — a boxed 120px button read as oversized chrome.
        back = compact_button(_t("settings.back").replace("←", "").strip())
        back.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Theme.MUTED};"
            f" border: none; border-radius: 8px; text-align: left; padding: 0 10px; }}"
            f"QPushButton:hover {{ background: {Theme.PANEL_2}; color: {Theme.TEXT}; }}"
        )
        back.clicked.connect(self.accept)
        nav_layout.addWidget(back)
        nav_layout.addSpacing(6)
        self.nav = QListWidget()
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # no focus ring on the nav
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setStyleSheet(
            f"""
            QListWidget {{ background: transparent; border: none; outline: none; }}
            QListWidget::item {{ padding: 9px 12px; margin: 1px 0; border: none; border-radius: 8px; }}
            QListWidget::item:hover {{ background: {Theme.PANEL_2}; }}
            QListWidget::item:selected {{ background: {Theme.SELECT}; color: {Theme.TEXT}; }}
            """
        )
        from dbaide.i18n import t as _t
        for label, key, icon_name in (
            (_t("settings.connections"), "connections", "database"),
            (_t("settings.models"), "models", "sparkles"),
            (_t("settings.resources"), "resources", "shield-check"),
            (_t("settings.integrations"), "integrations", "terminal"),
            (_t("settings.general"), "general", "settings"),
            (_t("settings.about"), "about", "info"),
        ):
            item = QListWidgetItem(label)
            item.setIcon(svg_icon(icon_name, color=Theme.TEXT_2, size=16, width=1.8))
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
        self.stack.addWidget(self._build_resources_page())
        self.stack.addWidget(self._build_integrations_page())
        self.stack.addWidget(self._build_general_page())
        self.stack.addWidget(self._build_about_page())
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        page_map = {
            "connections": 0, "models": 1, "model": 1, "resources": 2,
            "integrations": 3, "general": 4, "about": 5,
        }
        self.nav.setCurrentRow(page_map.get(initial_page, 0))
        self._reload_connection_list()
        self._reload_model_list()

    def _build_connections_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._page_header(_pt("settings.connections"), _pt("settings.connections.subtitle")))
        card = _SectionCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        row = QHBoxLayout()
        row.setSpacing(16)
        # Left column: list + list-level actions (New / Import).
        list_col = QVBoxLayout()
        list_col.setSpacing(8)
        self.conn_list = self._list_widget()
        self.conn_list.currentItemChanged.connect(self._on_connection_selected)
        list_col.addWidget(self.conn_list, 1)
        list_actions = QHBoxLayout()
        list_actions.setSpacing(6)
        self.add_conn_btn = compact_button(_pt("btn.new"), width=72)
        self.add_conn_btn.clicked.connect(self._add_connection)
        # Auto-fit width: "Import" (and longer localized labels) clip at a fixed 72px.
        self.import_conn_btn = compact_button(_pt("settings.import"))
        self.import_conn_btn.setToolTip(_pt("settings.import_conn_tooltip"))
        self.import_conn_btn.clicked.connect(self._import_connection)
        list_actions.addWidget(self.add_conn_btn)
        list_actions.addWidget(self.import_conn_btn)
        list_actions.addStretch(1)
        list_col.addLayout(list_actions)
        row.addLayout(list_col)
        # Right column: either the DB connection form (host/path) or, for an Excel
        # collection, the workbook manager. Only one is visible at a time.
        form_col = QVBoxLayout()
        self._conn_form_area = QWidget()
        form_area = QVBoxLayout(self._conn_form_area)
        form_area.setContentsMargins(0, 0, 0, 0)
        self.conn_form = ConnectionForm()
        form_area.addWidget(self.conn_form, 1)
        self.conn_test_status = QLabel("")
        self.conn_test_status.setWordWrap(True)
        self.conn_test_status.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
        form_area.addWidget(self.conn_test_status)
        form_area.addLayout(self._conn_actions())
        self.workbook_panel = _WorkbookPanel()
        self.workbook_panel.add_requested.connect(self._excel_add_workbook)
        self.workbook_panel.remove_requested.connect(self._excel_remove_workbook)
        self.workbook_panel.rename_requested.connect(self._excel_rename_workbook)
        self.workbook_panel.reimport_requested.connect(self._excel_reimport_workbook)
        self.workbook_panel.preview_requested.connect(self._excel_preview)
        self.workbook_panel.hide()
        form_col.addWidget(self._conn_form_area, 1)
        form_col.addWidget(self.workbook_panel, 1)
        row.addLayout(form_col, 1)
        card_layout.addLayout(row)
        layout.addWidget(card, 1)
        return page

    def _build_models_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._page_header(_pt("settings.models"), _pt("settings.models.subtitle")))
        ssl_note = QLabel(_pt("settings.models.ssl_note"))
        ssl_note.setWordWrap(True)
        ssl_note.setStyleSheet(f"color: {Theme.MUTED}; font-size: 12px;")
        layout.addWidget(ssl_note)
        card = _SectionCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        row = QHBoxLayout()
        row.setSpacing(16)
        # Left column: list + list-level actions (New).
        list_col = QVBoxLayout()
        list_col.setSpacing(8)
        self.model_list = self._list_widget()
        self.model_list.currentItemChanged.connect(self._on_model_selected)
        list_col.addWidget(self.model_list, 1)
        model_list_actions = QHBoxLayout()
        model_list_actions.setSpacing(6)
        self.add_model_btn = compact_button(_pt("btn.new"), width=72)
        self.add_model_btn.clicked.connect(self._add_model)
        model_list_actions.addWidget(self.add_model_btn)
        model_list_actions.addStretch(1)
        list_col.addLayout(model_list_actions)
        row.addLayout(list_col)
        # Right column: form + status + form-level actions (Save / Test / More).
        form_col = QVBoxLayout()
        self.model_form = ModelForm()
        form_col.addWidget(self.model_form, 1)
        self.model_test_status = QLabel("")
        self.model_test_status.setWordWrap(True)
        self.model_test_status.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
        form_col.addWidget(self.model_test_status)
        form_col.addLayout(self._model_actions())
        row.addLayout(form_col, 1)
        card_layout.addLayout(row)
        layout.addWidget(card, 1)
        return page

    def _build_resources_page(self) -> QWidget:
        from PyQt6.QtWidgets import QFormLayout, QScrollArea, QSpinBox

        from dbaide.desktop.components.inputs import Combo

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        from dbaide.i18n import t as _t
        layout.addWidget(self._page_header(
            _t("settings.resources.title"), _t("settings.resources.subtitle"),
        ))
        card = _SectionCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        inner.setObjectName("resourceFormInner")
        inner.setStyleSheet(
            f"QWidget#resourceFormInner {{ background: transparent; }}{FORM_INNER_LABEL_RULES}"
        )
        form = QFormLayout(inner)
        configure_form(form)

        prod = self._resource_presets.get("production", {})
        self._resource_spins: dict[str, QSpinBox] = {}
        self._resource_baselines: dict[str, int] = {}

        # Global app concurrency cap — distinct from the per-run database knobs below.
        from dbaide.config import DEFAULT_MAX_CONCURRENT_RUNS, DEFAULT_MAX_INFLIGHT_COST
        group_header = QLabel(_t("res.group.app"))
        group_header.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: 12px; font-weight: 600;"
            f" padding: 4px 0 2px 0;"
        )
        form.addRow(group_header)
        conc_spin = QSpinBox()
        conc_spin.setRange(1, 16)
        conc_spin.setMinimumWidth(120)
        conc_spin.setMaximumWidth(150)
        configure_compact_field(conc_spin, height=STANDARD_FIELD_HEIGHT, max_width=150)
        conc_cur = self._resource_values.get("max_concurrent_runs")
        conc_spin.setValue(int(conc_cur) if conc_cur not in (None, "") else DEFAULT_MAX_CONCURRENT_RUNS)
        self._resource_spins["max_concurrent_runs"] = conc_spin
        self._resource_baselines["max_concurrent_runs"] = DEFAULT_MAX_CONCURRENT_RUNS
        form.addRow(form_label(_t("res.max_concurrent_runs")), conc_spin)
        note = QLabel(_t("res.per_run_note"))
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px; padding: 2px 0 8px 0;")
        form.addRow("", note)

        cost_spin = QSpinBox()
        cost_spin.setRange(0, 1_000_000_000)
        cost_spin.setSingleStep(10_000)
        cost_spin.setGroupSeparatorShown(True)
        cost_spin.setMinimumWidth(120)
        cost_spin.setMaximumWidth(150)
        configure_compact_field(cost_spin, height=STANDARD_FIELD_HEIGHT, max_width=150)
        cost_cur = self._resource_values.get("max_inflight_cost")
        cost_spin.setValue(int(cost_cur) if cost_cur not in (None, "") else DEFAULT_MAX_INFLIGHT_COST)
        self._resource_spins["max_inflight_cost"] = cost_spin
        self._resource_baselines["max_inflight_cost"] = DEFAULT_MAX_INFLIGHT_COST
        form.addRow(form_label(_t("res.max_inflight_cost")), cost_spin)
        cost_note = QLabel(_t("res.max_inflight_cost_note"))
        cost_note.setWordWrap(True)
        cost_note.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px; padding: 2px 0 8px 0;")
        form.addRow("", cost_note)

        self._optimize_mode_combo = Combo()
        for value in ("gate", "suggest", "off"):
            self._optimize_mode_combo.addItem(_t(f"res.optimize_mode.{value}"), value)
        configure_compact_field(self._optimize_mode_combo, height=STANDARD_FIELD_HEIGHT, max_width=220)
        cur_mode = str(self._resource_values.get("optimize_advise_mode") or "gate")
        idx = self._optimize_mode_combo.findData(cur_mode)
        self._optimize_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow(form_label(_t("res.optimize_advise_mode")), self._optimize_mode_combo)
        mode_note = QLabel(_t("res.optimize_advise_mode_note"))
        mode_note.setWordWrap(True)
        mode_note.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px; padding: 2px 0 8px 0;")
        form.addRow("", mode_note)

        for group_key, fields in self._RESOURCE_GROUPS:
            header = QLabel(_t(group_key))
            header.setStyleSheet(
                f"color: {Theme.TEXT}; font-size: 12px; font-weight: 600;"
                f" padding: 10px 0 2px 0;"
            )
            form.addRow(header)
            for key, lo, hi in fields:
                spin = QSpinBox()
                spin.setRange(lo, hi)
                spin.setMinimumWidth(120)
                spin.setMaximumWidth(150)
                configure_compact_field(spin, height=STANDARD_FIELD_HEIGHT, max_width=150)
                baseline = int(prod.get(key, lo))
                self._resource_baselines[key] = baseline
                current = self._resource_values.get(key)
                spin.setValue(int(current) if current not in (None, "") else baseline)
                self._resource_spins[key] = spin
                form.addRow(form_label(_t(f"res.{key}")), spin)

        scroll.setWidget(inner)
        card_layout.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        reset = compact_button(_pt("btn.reset_defaults"), width=150)
        save = compact_button(_pt("btn.save"), primary=True, width=96)
        reset.clicked.connect(self._reset_resources)
        save.clicked.connect(self._save_resources)
        actions.addWidget(reset)
        actions.addWidget(save)
        card_layout.addLayout(actions)

        layout.addWidget(card, 1)
        return page

    def _reset_resources(self) -> None:
        # Back to the load-profile defaults (which persists no overrides).
        for key, spin in getattr(self, "_resource_spins", {}).items():
            spin.setValue(self._resource_baselines.get(key, spin.value()))

    def _save_resources(self) -> None:
        # Persist only values the user changed away from the profile default.
        values: dict = {}
        # Preserve string knobs that have no widget here (set via config.toml) so saving
        # this page doesn't wipe them — the backend replaces the whole resource_defaults.
        for key in ("optimizer_model", "build_profile_mode"):
            if self._resource_values.get(key):
                values[key] = self._resource_values[key]
        for key, spin in getattr(self, "_resource_spins", {}).items():
            if int(spin.value()) != self._resource_baselines.get(key):
                values[key] = int(spin.value())
        combo = getattr(self, "_optimize_mode_combo", None)
        if combo is not None and combo.currentData() != "gate":   # "gate" is the default
            values["optimize_advise_mode"] = combo.currentData()
        self._resource_values = values
        self.resource_saved.emit({"values": values})

    # ── Integrations page ──────────────────────────────────────────────────

    _TOOL_ICONS_DIR = Path(__file__).resolve().parents[1] / "assets" / "tool_icons"

    @staticmethod
    def _load_tool_icon(tool: str, size: int = 22) -> "QPixmap":
        """Load a tool's brand icon from assets, scaled to *size* with rounded corners."""
        from PyQt6.QtGui import QPixmap, QPainter, QPainterPath
        icon_path = SettingsDialog._TOOL_ICONS_DIR / f"{tool}.png"
        if not icon_path.exists():
            px = QPixmap(size, size)
            px.fill(Qt.GlobalColor.transparent)
            return px
        dpr = 2.0
        hw = int(round(size * dpr))
        source = QPixmap(str(icon_path))
        scaled = source.scaled(
            QSize(hw, hw),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        radius = hw * 0.22
        result = QPixmap(hw, hw)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(hw), float(hw), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        result.setDevicePixelRatio(dpr)
        return result

    def _build_integrations_page(self) -> QWidget:
        from dbaide.i18n import t
        from dbaide.desktop.components.inputs import Combo
        from dbaide.skill import TOOL_REGISTRY, SUPPORTED_TOOLS, installed_mode, VALID_MODES

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._page_header(
            t("settings.integrations"),
            t("settings.integrations.subtitle"),
        ))

        # ── Top bar: mode selector + install all + help ────────────────
        bar = QWidget()
        bar.setStyleSheet(
            f"QWidget {{ background: {Theme.PANEL}; border-radius: 8px; }}"
        )
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        bar_layout.setSpacing(10)

        mode_label = QLabel(t("settings.integrations.mode"))
        mode_label.setStyleSheet(
            f"color: {Theme.TEXT_2}; font-size: 12px; background: transparent;"
        )
        bar_layout.addWidget(mode_label)

        self._mode_combo = Combo()
        configure_compact_field(self._mode_combo, height=STANDARD_FIELD_HEIGHT)
        self._mode_combo.setFixedWidth(180)
        self._mode_labels = {
            "full": t("settings.integrations.mode.full"),
            "ask": t("settings.integrations.mode.ask"),
            "tools": t("settings.integrations.mode.tools"),
        }
        for mode_key in VALID_MODES:
            self._mode_combo.addItem(self._mode_labels[mode_key], mode_key)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_combo_changed)
        bar_layout.addWidget(self._mode_combo)

        bar_layout.addStretch(1)

        install_all_btn = compact_button(
            t("settings.integrations.install_all"), primary=True, width=100,
        )
        install_all_btn.clicked.connect(self._on_install_all_integrations)
        bar_layout.addWidget(install_all_btn)

        help_btn = IconToolButton(
            svg_icon("circle-help", color=Theme.MUTED, size=16),
            t("settings.integrations.help_tooltip"),
        )
        help_btn.setObjectName("integrationsHelpBtn")
        help_btn.setIconSize(QSize(16, 16))
        help_btn.setFixedSize(22, 22)
        help_btn.setStyleSheet("background: transparent;")
        help_btn.clicked.connect(self._show_integrations_help)
        bar_layout.addWidget(help_btn)

        layout.addWidget(bar)

        # ── Tool list ──────────────────────────────────────────────────
        card = _SectionCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(0)

        self._integration_rows: dict[str, dict] = {}
        for i, tool in enumerate(SUPPORTED_TOOLS):
            config_rel = TOOL_REGISTRY[tool]
            cur_mode = installed_mode(tool)

            row = QWidget()
            row.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 7, 8, 7)
            rl.setSpacing(10)

            icon_label = QLabel()
            icon_label.setFixedSize(22, 22)
            icon_label.setPixmap(self._load_tool_icon(tool))
            rl.addWidget(icon_label)

            name_label = QLabel(tool.capitalize())
            name_label.setFixedWidth(80)
            name_label.setStyleSheet(
                f"color: {Theme.TEXT}; font-size: 13px; font-weight: 600;"
            )
            rl.addWidget(name_label)

            path_label = QLabel(f"~/{config_rel}")
            path_label.setStyleSheet(f"color: {Theme.MUTED}; font-size: 11px;")
            rl.addWidget(path_label, 1)

            mode_tag = QLabel()
            mode_tag.setFixedWidth(46)
            mode_tag.setFixedHeight(18)
            mode_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rl.addWidget(mode_tag)

            status_label = QLabel()
            status_label.setFixedWidth(20)
            status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rl.addWidget(status_label)

            action_btn = compact_button("", width=80)
            action_btn.setFixedHeight(24)
            rl.addWidget(action_btn)

            self._integration_rows[tool] = {
                "status": status_label,
                "mode_tag": mode_tag,
                "btn": action_btn,
                "installed_mode": cur_mode,
            }
            self._refresh_integration_row(tool)
            action_btn.clicked.connect(
                lambda checked, t=tool: self._on_toggle_integration(t)
            )

            card_layout.addWidget(row)
            if i < len(SUPPORTED_TOOLS) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet(
                    f"background: {Theme.BORDER_SOFT}; max-height: 1px;"
                )
                card_layout.addWidget(sep)

        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _get_integration_mode(self) -> str:
        return self._mode_combo.currentData() or "full"

    def _on_mode_combo_changed(self, _index: int) -> None:
        for tool in self._integration_rows:
            self._refresh_integration_row(tool)

    def _show_integrations_help(self) -> None:
        from dbaide.i18n import t

        dialog_alert(
            self,
            t("settings.integrations.help.title"),
            t("settings.integrations.help.body"),
            max_body_height=520,
        )

    def _refresh_integration_row(self, tool: str) -> None:
        from dbaide.i18n import t

        info = self._integration_rows[tool]
        cur_mode = info["installed_mode"]
        installed = cur_mode is not None
        combo_mode = self._get_integration_mode()

        if installed:
            info["status"].setText("✓")
            info["status"].setStyleSheet(
                "color: #22c55e; font-size: 13px; font-weight: 700;"
            )
            info["mode_tag"].setText(cur_mode)
            info["mode_tag"].setStyleSheet(
                f"color: {Theme.ACCENT}; font-size: 10px; font-weight: 600;"
                f"background: {Theme.PANEL_2}; border-radius: 4px;"
                f"padding: 1px 4px;"
            )
            if cur_mode == combo_mode:
                info["btn"].setText(t("settings.integrations.uninstall"))
            else:
                info["btn"].setText(t("settings.integrations.reinstall"))
        else:
            info["status"].setText("—")
            info["status"].setStyleSheet(
                f"color: {Theme.MUTED}; font-size: 13px;"
            )
            info["mode_tag"].setText("")
            info["mode_tag"].setStyleSheet("background: transparent;")
            info["btn"].setText(t("settings.integrations.install"))

    def _on_toggle_integration(self, tool: str) -> None:
        from dbaide.skill import setup_tool, uninstall_tool
        from dbaide.i18n import t

        info = self._integration_rows[tool]
        cur_mode = info["installed_mode"]
        combo_mode = self._get_integration_mode()

        try:
            if cur_mode is not None and cur_mode == combo_mode:
                uninstall_tool(tool)
                info["installed_mode"] = None
            else:
                setup_tool(tool, mode=combo_mode)
                info["installed_mode"] = combo_mode
            self._refresh_integration_row(tool)
        except Exception as exc:
            dialog_warn(
                self, "DBAide",
                t("settings.integrations.error", error=str(exc)),
            )

    def _on_install_all_integrations(self) -> None:
        from dbaide.skill import setup_all, installed_mode
        from dbaide.i18n import t

        mode = self._get_integration_mode()
        try:
            setup_all(mode=mode)
        except Exception as exc:
            dialog_warn(
                self, "DBAide",
                t("settings.integrations.error", error=str(exc)),
            )
        for tool in self._integration_rows:
            self._integration_rows[tool]["installed_mode"] = installed_mode(tool)
            self._refresh_integration_row(tool)

    def _build_general_page(self) -> QWidget:
        from PyQt6.QtWidgets import QFormLayout
        from dbaide.desktop.components.inputs import Combo
        from dbaide.i18n import LANGUAGE_NAMES, t

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._page_header(t("settings.general"), t("settings.language.hint")))
        card = _SectionCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        form = QFormLayout()
        form.setSpacing(10)
        self.language_select = Combo()
        configure_compact_field(self.language_select, height=STANDARD_FIELD_HEIGHT)
        for code in ("en", "zh"):
            self.language_select.addItem(LANGUAGE_NAMES[code], code)
        idx = max(0, self.language_select.findData(self._language))
        self.language_select.setCurrentIndex(idx)
        self.language_select.currentIndexChanged.connect(
            lambda _i: self.language_changed.emit(self.language_select.currentData())
        )
        form.addRow(t("settings.language"), self.language_select)

        self.theme_select = Combo()
        configure_compact_field(self.theme_select, height=STANDARD_FIELD_HEIGHT)
        self.theme_select.addItem(t("settings.theme.dark"), "dark")
        self.theme_select.addItem(t("settings.theme.light"), "light")
        idx_t = max(0, self.theme_select.findData(self._theme))
        self.theme_select.setCurrentIndex(idx_t)
        self.theme_select.currentIndexChanged.connect(
            lambda _i: self.theme_changed.emit(self.theme_select.currentData())
        )
        form.addRow(t("settings.theme"), self.theme_select)

        from PyQt6.QtWidgets import QCheckBox
        self.stream_answers_check = QCheckBox(t("settings.stream_answers.label"))
        self.stream_answers_check.setChecked(self._stream_answers)
        self.stream_answers_check.toggled.connect(self.stream_answers_changed.emit)
        form.addRow(t("settings.stream_answers"), self.stream_answers_check)

        self.debug_trace_check = QCheckBox(t("settings.debug_trace.label"))
        self.debug_trace_check.setChecked(self._debug_trace)
        self.debug_trace_check.toggled.connect(self.debug_trace_changed.emit)
        form.addRow(t("settings.debug_trace"), self.debug_trace_check)

        card_layout.addLayout(form)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_about_page(self) -> QWidget:
        from dbaide.i18n import t

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._page_header(t("settings.about"), t("settings.about.subtitle")))

        card = _SectionCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(14)

        title = QLabel(f"{APP_NAME}  v{app_version()}")
        title.setStyleSheet(f"color: {Theme.TEXT}; font-size: 20px; font-weight: 800;")
        card_layout.addWidget(title)

        tagline = QLabel(t("settings.about.tagline"))
        tagline.setWordWrap(True)
        tagline.setStyleSheet(f"color: {Theme.MUTED}; font-size: 13px;")
        card_layout.addWidget(tagline)

        card_layout.addWidget(self._about_meta_row(t("settings.about.version"), f"v{app_version()}"))
        latest_row = QWidget()
        latest_layout = QHBoxLayout(latest_row)
        latest_layout.setContentsMargins(0, 0, 0, 0)
        latest_layout.setSpacing(12)
        latest_key = QLabel(t("settings.about.latest_version"))
        latest_key.setFixedWidth(88)
        latest_key.setStyleSheet(f"color: {Theme.MUTED}; font-size: 12px;")
        self._about_latest_value = QLabel(t("settings.about.latest_checking"))
        self._about_latest_value.setStyleSheet(f"color: {Theme.TEXT}; font-size: 12px;")
        latest_layout.addWidget(latest_key)
        latest_layout.addWidget(self._about_latest_value, 1)
        self._about_latest_row = latest_row
        card_layout.addWidget(self._about_latest_row)
        self._about_latest_url = ""
        self._about_latest_link = ghost_action_button(
            "",
            icon=svg_icon("external-link", color=Theme.BLUE, size=14),
            tooltip="",
        )
        self._about_latest_link.hide()
        self._about_latest_link.clicked.connect(self._open_about_latest_release)
        card_layout.addWidget(self._about_latest_link)
        card_layout.addWidget(self._about_link_row(
            t("settings.about.developer"),
            DEVELOPER_NAME,
            DEVELOPER_URL,
        ))
        card_layout.addWidget(self._about_meta_row(t("settings.about.license"), LICENSE_NAME))

        links_head = QLabel(t("settings.about.links"))
        links_head.setStyleSheet(
            f"color: {Theme.MUTED}; font-size: 11px; font-weight: 600; margin-top: 6px;"
        )
        card_layout.addWidget(links_head)

        links_col = QVBoxLayout()
        links_col.setContentsMargins(0, 0, 0, 0)
        links_col.setSpacing(2)
        for label_key, url in project_links():
            links_col.addWidget(self._about_external_link(t(label_key), url))
        card_layout.addLayout(links_col)

        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def set_release_check_result(
        self,
        *,
        ok: bool,
        current_version: str = "",
        latest_version: str = "",
        update_available: bool = False,
        ahead_of_release: bool = False,
        release_url: str = "",
    ) -> None:
        from dbaide.i18n import t

        self._about_latest_url = str(release_url or "").strip()
        if not ok:
            text = t("settings.about.latest_unavailable")
            self._about_latest_value.setText(text)
            self._about_latest_link.hide()
            return
        latest = str(latest_version or "").strip()
        if update_available and latest:
            text = t("settings.about.latest_available", version=latest)
        elif ahead_of_release and latest:
            text = t("settings.about.latest_ahead", version=latest)
        elif latest:
            text = t("settings.about.latest_up_to_date", version=latest)
        else:
            text = t("settings.about.latest_unavailable")
        self._about_latest_value.setText(text)
        if update_available and latest and self._about_latest_url:
            self._about_latest_link.setText(t("settings.about.latest_available", version=latest))
            self._about_latest_link.setToolTip(self._about_latest_url)
            self._about_latest_link.show()
        else:
            self._about_latest_link.hide()

    def _open_about_latest_release(self) -> None:
        url = str(getattr(self, "_about_latest_url", "") or "").strip()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @staticmethod
    def _set_about_meta_value(row: QWidget, value: str) -> None:
        labels = row.findChildren(QLabel)
        if len(labels) >= 2:
            labels[1].setText(value)

    @staticmethod
    def _about_meta_row(label: str, value: str) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        key = QLabel(label)
        key.setFixedWidth(88)
        key.setStyleSheet(f"color: {Theme.MUTED}; font-size: 12px;")
        val = QLabel(value)
        val.setStyleSheet(f"color: {Theme.TEXT}; font-size: 12px;")
        h.addWidget(key)
        h.addWidget(val, 1)
        return row

    @staticmethod
    def _about_link_row(label: str, text: str, url: str) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        key = QLabel(label)
        key.setFixedWidth(88)
        key.setStyleSheet(f"color: {Theme.MUTED}; font-size: 12px;")
        btn = ghost_action_button(
            text,
            icon=svg_icon("external-link", color=Theme.MUTED, size=14),
            tooltip=url,
        )
        btn.clicked.connect(lambda _checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))
        h.addWidget(key)
        h.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        h.addStretch(1)
        return row

    @staticmethod
    def _about_external_link(label: str, url: str) -> QWidget:
        btn = ghost_action_button(
            label,
            icon=svg_icon("external-link", color=Theme.MUTED, size=14),
            tooltip=url,
        )
        btn.clicked.connect(lambda _checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))
        return btn

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
                border-radius: 8px;
            }}
            QListWidget::item {{ padding: 10px 12px; }}
            QListWidget::item:hover {{ background: {Theme.PANEL_2}; }}
            QListWidget::item:selected {{ background: {Theme.SELECT}; }}
            """
        )
        return widget

    def _conn_actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.save_conn_btn = compact_button(_pt("btn.save"), primary=True, width=80)
        self.save_conn_btn.clicked.connect(self._save_connection)
        self.test_conn_btn = compact_button(_pt("btn.test"), width=72)
        self.test_conn_btn.clicked.connect(self._test_connection)
        self.conn_more = MenuButton(
            _pt("settings.more"),
            max_width=92,
            icon=more_icon(color=Theme.TEXT_2, size=15),
            filled=True,
        )
        self.conn_more.add_action(_pt("settings.set_default"), self._set_default_connection)
        self.conn_more.add_action(_pt("settings.export_conn"), self._export_connection)
        self.conn_more.add_separator()
        self.conn_more.add_action(_pt("settings.export_all"), self._export_all)
        self.conn_more.add_separator()
        self.conn_more.add_action(_pt("settings.remove"), self._remove_connection)
        actions.addStretch(1)
        actions.addWidget(self.save_conn_btn)
        actions.addWidget(self.test_conn_btn)
        actions.addWidget(self.conn_more)
        return actions

    def _model_actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.save_model_btn = compact_button(_pt("btn.save"), primary=True, width=80)
        self.save_model_btn.clicked.connect(self._save_model)
        self.test_model_btn = compact_button(_pt("btn.test"), width=72)
        self.test_model_btn.clicked.connect(self._test_model)
        self.model_more = MenuButton(
            _pt("settings.more"),
            max_width=92,
            icon=more_icon(color=Theme.TEXT_2, size=15),
            filled=True,
        )
        self.model_more.add_action(_pt("settings.set_default"), self._set_default_model)
        self.model_more.add_action(_pt("settings.remove"), self._remove_model)
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
            # No selection callback fires for an empty list, so reset the right pane
            # ourselves — otherwise a just-deleted Excel collection's panel lingers.
            self._show_conn_form()
            self.conn_form.clear()
            self._set_connection_new_mode(False)

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
            self._set_model_new_mode(False)

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

    def _select_draft(self, widget: QListWidget, key: str, label: str) -> None:
        self._remove_draft(widget, key)
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        widget.insertItem(0, item)
        widget.setCurrentItem(item)

    def _remove_draft(self, widget: QListWidget, key: str) -> None:
        for i in range(widget.count()):
            item = widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                widget.takeItem(i)
                return

    def _set_connection_new_mode(self, new: bool) -> None:
        self.save_conn_btn.setText(_pt("btn.create") if new else _pt("btn.save"))
        self.save_conn_btn.setIcon(
            svg_icon("check" if new else "save", color=button_icon_color(primary=True), size=14)
        )
        key = self._selected_list_key(self.conn_list)
        self.conn_more.setEnabled((not new) and bool(key))

    def _set_model_new_mode(self, new: bool) -> None:
        self.save_model_btn.setText(_pt("btn.create") if new else _pt("btn.save"))
        self.save_model_btn.setIcon(
            svg_icon("check" if new else "save", color=button_icon_color(primary=True), size=14)
        )
        key = self._selected_list_key(self.model_list)
        self.model_more.setEnabled((not new) and bool(key))

    def _selected_list_key(self, widget: QListWidget) -> str:
        item = widget.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _on_connection_selected(self, current, _previous) -> None:
        if not current:
            return
        name = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if name == _NEW_CONNECTION_ID:
            self._show_conn_form()
            self._selected_conn = ""
            self.conn_form.clear()
            self.conn_test_status.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
            self.conn_test_status.setText(_pt("settings.new_connection_hint"))
            self._set_connection_new_mode(True)
            return
        self._remove_draft(self.conn_list, _NEW_CONNECTION_ID)
        self._selected_conn = name
        collection = self._collection_for(name)
        if collection is not None:
            self._show_excel_panel(name, collection)
            return
        self._show_conn_form()
        self.conn_form.load(self._connections.get(name))
        self.conn_test_status.clear()
        self._set_connection_new_mode(False)

    # ── Excel collections ─────────────────────────────────────────────────────

    def _collection_for(self, name: str):
        if self._config_dir is None:
            return None
        from dbaide.ingest import collection_for_connection
        path = (self._connections.get(name) or {}).get("path") or ""
        return collection_for_connection(self._config_dir, path)

    def _current_collection(self):
        return self._collection_for(self._selected_conn) if self._selected_conn else None

    def _show_conn_form(self) -> None:
        self.workbook_panel.hide()
        self._conn_form_area.show()

    def _show_excel_panel(self, name: str, collection) -> None:
        self._conn_form_area.hide()
        self.conn_more.setEnabled(False)   # the form-level actions don't apply here
        self.workbook_panel.load(name, collection.workbooks())
        self.workbook_panel.show()

    def _run_import(self, work, on_done) -> None:
        """Run an Excel import on a worker thread behind a modal busy dialog, then call
        on_done(result) on the UI thread (or surface the error). The modal dialog blocks
        interaction with Settings until the import finishes, so the worker can't be orphaned."""
        dlg = QProgressDialog(_pt("excel.importing"), None, 0, 0, self)  # no cancel; indeterminate
        dlg.setWindowTitle(_pt("excel.importing_title"))
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        worker = _ImportWorker(work, self)
        worker.progress.connect(dlg.setLabelText)

        def finish(result=None, exc=None):
            worker.wait()
            dlg.close()
            worker.deleteLater()
            self._import_worker = None
            if exc is not None:
                dialog_warn(self, _pt("settings.title"), _pt("excel.err.import_failed", error=str(exc)))
            else:
                on_done(result)

        worker.done.connect(lambda r: finish(result=r))
        worker.failed.connect(lambda e: finish(exc=e))
        self._import_worker = worker      # keep a reference so the QThread isn't GC'd
        dlg.show()
        worker.start()

    def _create_excel_collection(self) -> None:
        from dbaide.desktop.dialogs.excel_collection import new_collection
        from dbaide.ingest import collection_dir, import_workbooks

        if self._config_dir is None:
            return
        chosen = new_collection(self, set(self._connections))
        if not chosen:
            return
        name, specs = chosen
        dest = collection_dir(self._config_dir, name)

        def done(result):
            self._note_skipped(result)
            # Register through the normal save path; once the list reloads and re-selects this
            # connection, _on_connection_selected detects the collection and shows the panel.
            self._selected_conn = name
            self.connection_saved.emit({
                "name": name, "type": "sqlite", "path": str(result.db_path),
                "make_default": not self._connections,
            })

        self._run_import(lambda progress: import_workbooks(specs, dest_dir=dest, on_progress=progress), done)

    def _excel_add_workbook(self) -> None:
        from dbaide.desktop.dialogs.excel_collection import add_collection_files

        collection = self._current_collection()
        if collection is None:
            return
        specs = add_collection_files(self)   # staging: rename tables + pick headers, like create
        if not specs:
            return
        existing = {w.name for w in collection.workbooks()}
        clashes = [s.logical_name for s in specs if s.logical_name in existing]
        if clashes and not dialog_confirm(
            self, _pt("settings.title"), _pt("excel.confirm_overwrite", names=", ".join(clashes))
        ):
            return                       # decline → don't create same-name duplicates
        conn_name = self._selected_conn

        def done(result):
            self._note_skipped(result)
            self.workbook_panel.load(conn_name, collection.workbooks())
            self.excel_collection_changed.emit(conn_name)

        self._run_import(lambda progress: collection.add(specs, overwrite=bool(clashes), on_progress=progress), done)

    def _note_skipped(self, result) -> None:
        if getattr(result, "warnings", None):
            dialog_warn(self, _pt("settings.title"),
                        _pt("excel.skipped_sheets", n=len(result.warnings),
                            detail="\n".join(result.warnings)))

    def _excel_rename_workbook(self, workbook_id: str) -> None:
        from dbaide.desktop.dialogs.text_input import get_text

        collection = self._current_collection()
        if collection is None:
            return
        current = next((w for w in collection.workbooks() if w.id == workbook_id), None)
        if current is None:
            return
        new_name, ok = get_text(
            self, _pt("excel.rename_title"), _pt("excel.rename_prompt"), text=current.name
        )
        if not ok or not new_name.strip() or new_name.strip() == current.name:
            return
        try:
            collection.rename(workbook_id, new_name.strip())
        except Exception as exc:  # noqa: BLE001
            dialog_warn(self, _pt("settings.title"), _pt("excel.err.import_failed", error=str(exc)))
            return
        self.workbook_panel.load(self._selected_conn, collection.workbooks())
        self.excel_collection_changed.emit(self._selected_conn)

    def _excel_reimport_workbook(self, workbook_id: str) -> None:
        from dbaide.ingest import ImportSpec

        collection = self._current_collection()
        if collection is None:
            return
        wb = next((w for w in collection.workbooks() if w.id == workbook_id), None)
        if wb is None:
            return
        path = Path(wb.source_path) if wb.source_path else None
        if path is None or not path.exists():
            # the original file moved or is unknown → let the user point at it again
            picked, _ = get_open_file_name(
                self, _pt("excel.reimport_pick_title"), wb.source_path or "", _pt("excel.file_filter")
            )
            if not picked:
                return
            path = Path(picked)
        conn_name = self._selected_conn

        def done(result):
            self._note_skipped(result)
            self.workbook_panel.load(conn_name, collection.workbooks())
            self.excel_collection_changed.emit(conn_name)

        self._run_import(
            lambda progress: collection.add([ImportSpec(path, name=wb.name)], overwrite=True, on_progress=progress),
            done,
        )

    def _excel_preview(self) -> None:
        collection = self._current_collection()
        if collection is None:
            return
        from dbaide.desktop.dialogs.collection_preview import CollectionPreviewDialog
        CollectionPreviewDialog(self, collection, name=self._selected_conn).exec()

    def _excel_remove_workbook(self, workbook_id: str) -> None:
        collection = self._current_collection()
        if collection is None:
            return
        books = collection.workbooks()
        target = next((w for w in books if w.id == workbook_id), None)
        if target is None:
            return
        last = len(books) == 1
        prompt = "excel.confirm_remove_last" if last else "excel.confirm_remove"
        if not dialog_confirm(self, _pt("settings.title"), _pt(prompt, file=target.source_filename)):
            return
        if last:
            # Empty collection → drop the whole connection (controller cleans up the files).
            self.connection_deleted.emit(self._selected_conn)
            return
        try:
            collection.remove(workbook_id)
        except Exception as exc:  # noqa: BLE001
            dialog_warn(self, _pt("settings.title"), _pt("excel.err.import_failed", error=str(exc)))
            return
        self.workbook_panel.load(self._selected_conn, collection.workbooks())
        self.excel_collection_changed.emit(self._selected_conn)

    def _on_model_selected(self, current, _previous) -> None:
        if not current:
            return
        name = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if name == _NEW_MODEL_ID:
            self._selected_model = ""
            self.model_form.clear()
            self.model_test_status.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
            self.model_test_status.setText(_pt("settings.new_model_hint"))
            self._set_model_new_mode(True)
            return
        self._remove_draft(self.model_list, _NEW_MODEL_ID)
        self._selected_model = name
        self.model_form.load(self._models.get(name))
        self.model_test_status.clear()
        self._set_model_new_mode(False)

    def _add_connection(self) -> None:
        # When imports are available, first ask which kind of connection to create.
        if self._config_dir is not None:
            kind = choose_connection_kind(self)
            if not kind:
                return
            if kind == "excel":
                self._create_excel_collection()
                return
        self._selected_conn = ""
        self._select_draft(self.conn_list, _NEW_CONNECTION_ID, _pt("settings.new_connection"))

    def _save_connection(self) -> None:
        payload = self.conn_form.payload(make_default=not self._connections)
        if not payload["name"]:
            dialog_warn(self, _pt("settings.title"), _pt("settings.err.conn_name"))
            return
        # Remember which row to select; the controller updates the list only after
        # the save actually succeeds (no optimistic write that lies on failure).
        self._selected_conn = payload["name"]
        self.connection_saved.emit(payload)

    def _test_connection(self) -> None:
        payload = self.conn_form.payload()
        if not payload["name"]:
            dialog_warn(self, _pt("settings.title"), _pt("settings.err.select_conn_test"))
            return
        self.connection_test.emit(payload)

    def _set_default_connection(self) -> None:
        name = self.conn_form.payload()["name"]
        if not name or name not in self._connections:
            dialog_warn(self, _pt("settings.title"), _pt("settings.err.save_conn_first"))
            return
        payload = dict(self._connections[name])
        payload["make_default"] = True
        self.connection_saved.emit(payload)

    def _remove_connection(self) -> None:
        name = self.conn_form.payload()["name"]
        if not name or name not in self._connections:
            return
        if not dialog_confirm(self, _pt("settings.title"), _pt("settings.confirm.remove_conn", name=name)):
            return
        self.connection_deleted.emit(name)

    def remove_connection_entry(self, name: str) -> None:
        self._connections.pop(name, None)
        if self._default_connection == name:
            self._default_connection = next(iter(self._connections), "")
        self._selected_conn = ""
        self._reload_connection_list()

    def _add_model(self) -> None:
        self._selected_model = ""
        self._select_draft(self.model_list, _NEW_MODEL_ID, _pt("settings.new_model"))

    def _save_model(self) -> None:
        payload = self.model_form.payload(make_default=not self._models)
        if not payload["name"]:
            dialog_warn(self, _pt("settings.title"), _pt("settings.err.model_name"))
            return
        # The controller updates the list on save success; don't write optimistically.
        self._selected_model = payload["name"]
        self.model_saved.emit(payload)

    def _test_model(self) -> None:
        payload = self.model_form.payload()
        if not payload.get("name"):
            dialog_warn(self, _pt("settings.title"), _pt("settings.err.select_model_test"))
            return
        self.model_test.emit(payload)

    def _set_default_model(self) -> None:
        name = self.model_form.payload()["name"]
        if not name or name not in self._models:
            dialog_warn(self, _pt("settings.title"), _pt("settings.err.save_model_first"))
            return
        payload = dict(self._models[name])
        payload["make_default"] = True
        self.model_saved.emit(payload)

    def _remove_model(self) -> None:
        name = self.model_form.payload()["name"]
        if not name or name not in self._models:
            return
        if not dialog_confirm(self, _pt("settings.title"), _pt("settings.confirm.remove_model", name=name)):
            return
        self.model_deleted.emit(name)

    def remove_model_entry(self, name: str) -> None:
        self._models.pop(name, None)
        if self._default_model == name:
            self._default_model = next(iter(self._models), "")
        self._selected_model = ""
        self._reload_model_list()

    def set_save_busy(self, busy: bool, *, target: str = "connection") -> None:
        if target == "connection":
            self.save_conn_btn.setEnabled(not busy)
            self.test_conn_btn.setEnabled(not busy)
            self.add_conn_btn.setEnabled(not busy)
            self.import_conn_btn.setEnabled(not busy)
            key = self._selected_list_key(self.conn_list)
            self.conn_more.setEnabled((not busy) and bool(key) and key != _NEW_CONNECTION_ID)
            if busy:
                self.conn_test_status.setText(_pt("settings.saving_conn"))
                self.conn_test_status.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
        else:
            self.save_model_btn.setEnabled(not busy)
            self.test_model_btn.setEnabled(not busy)
            self.add_model_btn.setEnabled(not busy)
            key = self._selected_list_key(self.model_list)
            self.model_more.setEnabled((not busy) and bool(key) and key != _NEW_MODEL_ID)
            if busy:
                self.model_test_status.setText(_pt("settings.saving_model"))
                self.model_test_status.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")

    def set_test_busy(self, busy: bool, *, target: str = "connection") -> None:
        if target == "connection":
            self.test_conn_btn.setEnabled(not busy)
            self.save_conn_btn.setEnabled(not busy)
            self.add_conn_btn.setEnabled(not busy)
            self.import_conn_btn.setEnabled(not busy)
            key = self._selected_list_key(self.conn_list)
            self.conn_more.setEnabled((not busy) and bool(key) and key != _NEW_CONNECTION_ID)
            if busy:
                self.conn_test_status.setText(_pt("settings.testing_conn"))
                self.conn_test_status.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")
        else:
            self.test_model_btn.setEnabled(not busy)
            self.save_model_btn.setEnabled(not busy)
            self.add_model_btn.setEnabled(not busy)
            key = self._selected_list_key(self.model_list)
            self.model_more.setEnabled((not busy) and bool(key) and key != _NEW_MODEL_ID)
            if busy:
                self.model_test_status.setText(_pt("settings.testing_model"))
                self.model_test_status.setStyleSheet(f"color:{Theme.MUTED}; font-size:12px;")

    def show_test_result(self, ok: bool, message: str, *, target: str = "connection") -> None:
        label = self.conn_test_status if target == "connection" else self.model_test_status
        color = Theme.GREEN if ok else Theme.RED
        prefix = _pt("settings.test_ok") if ok else _pt("settings.test_failed")
        label.setStyleSheet(f"color:{color}; font-size:12px;")
        label.setText(f"{prefix}: {message}")
        if not ok:
            dialog_warn(self, _pt("settings.title"), message)

    # ── import / export ─────────────────────────────────────────────────────--

    def _export_connection(self) -> None:
        key = self._selected_list_key(self.conn_list)
        if not key or key == _NEW_CONNECTION_ID:
            dialog_warn(self, _pt("settings.title"), _pt("settings.err.save_conn_first"))
            return
        self.export_connection.emit(key)

    def _import_connection(self) -> None:
        path, _ = get_open_file_name(
            self, _pt("import.confirm_title"), "",
            _pt("import.file_filter"),
        )
        if path:
            self.import_requested.emit(path)

    def _export_all(self) -> None:
        self.export_all_requested.emit()

    def add_imported_connection(self, name: str) -> None:
        """Update the connection list after a successful import."""
        # The caller is expected to have already persisted the connection.
        # Refresh from the parent window's bootstrap data.
        if name and name not in self._connections:
            self._connections[name] = {"name": name, "type": "?"}
        self._selected_conn = name
        self._reload_connection_list()
