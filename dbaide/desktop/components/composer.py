from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QTextEdit, QVBoxLayout, QWidget

from dbaide.desktop.components.base import (
    button_icon_color,
    clear_layout_widgets,
    compact_button,
    Panel,
)
from dbaide.desktop.components.icons import svg_icon
from dbaide.desktop.components.inputs import configure_multiline_text_edit, sync_multiline_height
from dbaide.desktop.components.menu import PillSelect
from dbaide.desktop.components.spinner import BusyAnimator, spinner_icon
from dbaide.desktop.theme import Theme

# Start compact (~2 lines) and grow with content up to the max, rather than
# reserving 3 empty lines at rest.
_INPUT_MIN = 60
_INPUT_MAX = 200
_INPUT_PAD = 24


def _model_label(entry: dict) -> str:
    model_id = str(entry.get("model") or "").strip()
    name = str(entry.get("name") or "default")
    if model_id:
        return model_id if len(model_id) <= 24 else model_id[:22] + "…"
    if entry.get("provider") and entry.get("provider") != "none":
        return f"{name} ({entry['provider']})"
    return name


class ComposerWidget(Panel):
    submit_requested = pyqtSignal(str)
    stop_requested = pyqtSignal()
    model_changed = pyqtSignal(str)
    attach_requested = pyqtSignal()  # the "+" context button was clicked

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # One unified rounded container (input + toolbar share a single border that
        # lights up on focus) instead of a bordered input nested inside a panel.
        self.setObjectName("composer")
        self._focused = False
        self._apply_container_style()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)

        from dbaide.i18n import t
        self._running = False
        self.input = QTextEdit()
        # Clean, minimal placeholder (Codex-style); the keybind hint lives in the
        # tooltip rather than cluttering the field.
        self.input.setPlaceholderText(t("composer.placeholder.ready"))
        configure_multiline_text_edit(
            self.input,
            min_height=_INPUT_MIN,
            max_height=_INPUT_MAX,
            padding=_INPUT_PAD,
        )
        self.input.setStyleSheet(
            """
            QTextEdit {
                background: transparent;
                border: none;
                padding: 2px 2px;
                font-size: 14px;
            }
            """
        )
        self.input.textChanged.connect(self._sync_input_height)
        self.input.installEventFilter(self)
        outer.addWidget(self.input)

        # ── Context attachment chips (db/table the user pinned for this prompt) ──
        # The attached assets are injected into the model prompt at submit time but
        # NOT shown in the visible user message. Chips let the user see/remove them.
        self._attachments: list[dict] = []          # [{kind, path, name, database}]
        self._chips_row = QHBoxLayout()
        self._chips_row.setSpacing(6)
        self._chips_row.setContentsMargins(0, 0, 0, 0)
        self._chips_host = QWidget()
        self._chips_host.setLayout(self._chips_row)
        self._chips_host.setVisible(False)
        outer.addWidget(self._chips_host)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        # "+" context button — pick a database/table to attach as prompt context.
        self.attach_btn = compact_button("", width=30)
        self.attach_btn.setIcon(svg_icon("plus", color=Theme.TEXT_2, size=15))
        self.attach_btn.setIconSize(QSize(15, 15))
        self.attach_btn.setToolTip(t("composer.attach_tooltip"))
        self.attach_btn.clicked.connect(self.attach_requested.emit)
        footer.addWidget(self.attach_btn)
        footer.addStretch(1)
        self.model_select = PillSelect("Model", max_width=132)
        self.model_select.value_changed.connect(self.model_changed.emit)
        footer.addWidget(self.model_select)
        # Compact icon button (arrow-up to send; spinner while running) — smaller and
        # cleaner than a text button, matching the modern composer style.
        self.action_btn = compact_button("", primary=True, width=38)
        self.action_btn.setIcon(svg_icon("arrow-up", color=button_icon_color(primary=True), size=18))
        self.action_btn.setIconSize(QSize(18, 18))
        self.action_btn.setToolTip(t("composer.send"))
        self.action_btn.clicked.connect(self._on_action)
        footer.addWidget(self.action_btn)
        outer.addLayout(footer)
        # While running, the action button shows a spinning ring so it reads as a
        # live "stop" (icon, so the label never clips).
        self._busy = BusyAnimator(self._on_spin, parent=self)

    def _apply_container_style(self) -> None:
        border = Theme.FOCUS if self._focused else Theme.BORDER
        self.setStyleSheet(
            f"QFrame#composer {{ background: {Theme.PANEL}; border: 1px solid {border};"
            f" border-radius: {Theme.RADIUS_MD}px; }}"
        )

    def _set_focused(self, focused: bool) -> None:
        if focused != self._focused:
            self._focused = focused
            self._apply_container_style()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.input:
            if event.type() == QEvent.Type.Resize:
                self._sync_input_height()
            elif event.type() == QEvent.Type.FocusIn:
                self._set_focused(True)
            elif event.type() == QEvent.Type.FocusOut:
                self._set_focused(False)
            elif event.type() == QEvent.Type.KeyPress:
                mod = event.modifiers() & (
                    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
                )
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mod:
                    self._on_action()
                    return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_input_height()

    def _sync_input_height(self, *_args) -> None:
        sync_multiline_height(
            self.input,
            min_height=_INPUT_MIN,
            max_height=_INPUT_MAX,
            padding=_INPUT_PAD,
        )

    def set_models(self, models: list[dict], default: str = "") -> None:
        options: list[tuple[str, str]] = []
        for entry in models:
            name = str(entry.get("name") or "default")
            options.append((_model_label(entry), name))
        if not options:
            from dbaide.i18n import t
            options = [(t("composer.no_model"), "default")]
        self.model_select.set_options(options)
        active = default or (options[0][1] if options else "default")
        self.model_select.set_value(active)

    def set_running(self, running: bool) -> None:
        from dbaide.i18n import t
        self._running = running
        if running:
            self.action_btn.setToolTip(t("composer.stop"))
            self._busy.start()  # _on_spin paints the rotating ring icon
        else:
            self._busy.stop()
            self.action_btn.setIcon(
                svg_icon("arrow-up", color=button_icon_color(primary=True), size=18)
            )
            self.action_btn.setToolTip(t("composer.send"))
        self.input.setEnabled(not running)
        self.model_select.setEnabled(not running)
        self.attach_btn.setEnabled(not running)

    def _on_spin(self) -> None:
        self.action_btn.setIcon(
            spinner_icon(self._busy.angle, color=button_icon_color(primary=True))
        )

    def set_placeholder(self, text: str) -> None:
        self.input.setPlaceholderText(text)

    def clear_input(self) -> None:
        self.input.clear()
        self._sync_input_height()

    # ── context attachments ─────────────────────────────────────────────────--

    def add_attachment(self, *, kind: str, path: str, name: str, database: str = "") -> bool:
        """Pin a db/table as prompt context. Deduplicates by path. Returns True if
        newly added."""
        if any(a["path"] == path for a in self._attachments):
            return False
        self._attachments.append({"kind": kind, "path": path, "name": name, "database": database})
        self._render_chips()
        return True

    def attachments(self) -> list[dict]:
        return list(self._attachments)

    def clear_attachments(self) -> None:
        self._attachments.clear()
        self._render_chips()

    def _remove_attachment(self, path: str) -> None:
        self._attachments = [a for a in self._attachments if a["path"] != path]
        self._render_chips()

    def _render_chips(self) -> None:
        # Hide before deleteLater — never setParent(None); that spawns stray windows on macOS.
        clear_layout_widgets(self._chips_row)
        if not self._attachments:
            self._chips_host.setVisible(False)
            return
        for att in self._attachments:
            chip = _ContextChip(att["kind"], att["name"], att["path"])
            chip.removed.connect(self._remove_attachment)
            self._chips_row.addWidget(chip)
        self._chips_row.addStretch(1)
        self._chips_host.setVisible(True)

    def question(self) -> str:
        return self.input.toPlainText().strip()

    def mode(self) -> str:
        return "ask"

    def model_name(self) -> str:
        return self.model_select.value()

    def _on_action(self) -> None:
        # Decide by state, not button text (text is translated).
        if self._running:
            self.stop_requested.emit()
            return
        self.submit_requested.emit(self.question())


class _ContextChip(QWidget):
    """A removable chip showing an attached db/table context — icon · name · ✕.

    The ✕ is a clear, always-visible button with a hover highlight (not a faint
    glyph), so removing an attachment is obvious.
    """

    removed = pyqtSignal(str)  # path

    def __init__(self, kind: str, name: str, path: str, parent=None) -> None:
        from PyQt6.QtWidgets import QToolButton
        super().__init__(parent)
        self._path = path
        self.setObjectName("ctxChip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 4, 0)
        lay.setSpacing(5)
        icon = "database" if kind == "database" else "table"
        icon_lbl = QLabel()
        # 16px viewBox-based glyphs clip at very small sizes — render at 15 so the
        # database/table outline shows in full.
        icon_lbl.setPixmap(svg_icon(icon, color=Theme.BLUE, size=15).pixmap(QSize(15, 15)))
        icon_lbl.setFixedSize(15, 15)
        lay.addWidget(icon_lbl)
        text = QLabel(name)
        text.setStyleSheet(f"color: {Theme.TEXT}; font-size: 12px;")
        text.setToolTip(f"{kind}: {path}")
        lay.addWidget(text)
        close = QToolButton()
        close.setObjectName("ctxChipClose")
        close.setIcon(svg_icon("x", color=Theme.MUTED, size=12, width=2.2))
        close.setIconSize(QSize(12, 12))
        close.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        from dbaide.i18n import t as _ct
        close.setToolTip(_ct("composer.remove"))
        close.setFixedSize(18, 18)
        close.clicked.connect(lambda: self.removed.emit(self._path))
        lay.addWidget(close)
        self.setFixedHeight(26)
        # Scope the pill styling to the chip itself (#ctxChip) so the border/
        # background does NOT cascade onto the child icon/label/button — an unscoped
        # rule leaks the border, producing a "double box" and hiding the ✕.
        self.setStyleSheet(
            f"QWidget#ctxChip {{ background: {Theme.PANEL_2};"
            f" border: 1px solid {Theme.BORDER}; border-radius: 8px; }}"
            f"QWidget#ctxChip QLabel {{ background: transparent; border: none; }}"
            # padding:0 / min-width:0 are essential — the global QToolButton rule
            # sets `padding: 0 10px; min-height: 26px`, which would leave no room
            # for the compact close icon.
            f"QToolButton#ctxChipClose {{ background: transparent; border: none;"
            f" border-radius: 7px; color: {Theme.MUTED}; font-size: 13px;"
            f" padding: 0; min-width: 0; min-height: 0; }}"
            f"QToolButton#ctxChipClose:hover {{ background: {Theme.PANEL_3};"
            f" color: {Theme.TEXT}; }}"
        )
