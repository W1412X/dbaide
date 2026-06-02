from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QHBoxLayout, QTextEdit, QVBoxLayout

from dbaide.desktop.components.base import compact_button, Panel
from dbaide.desktop.components.composer_options import POLICIES, POLICY_TOOLTIPS
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
    submit_requested = pyqtSignal(str, str)
    stop_requested = pyqtSignal()
    model_changed = pyqtSignal(str)

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
        self.input.setPlaceholderText(t("composer.placeholder.ready") + t("composer.hint"))
        configure_multiline_text_edit(
            self.input,
            min_height=_INPUT_MIN,
            max_height=_INPUT_MAX,
            padding=_INPUT_PAD,
        )
        self.input.setStyleSheet(
            f"""
            QTextEdit {{
                background: transparent;
                border: none;
                padding: 2px 2px;
                font-size: 14px;
            }}
            """
        )
        self.input.textChanged.connect(self._sync_input_height)
        self.input.installEventFilter(self)
        outer.addWidget(self.input)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.policy_select = PillSelect("Safe", max_width=108)
        self.policy_select.set_options(POLICIES)
        self.policy_select.set_option_tooltips(POLICY_TOOLTIPS)
        self.policy_select.set_value("safe_auto")
        footer.addWidget(self.policy_select)
        footer.addStretch(1)
        self.model_select = PillSelect("Model", max_width=132)
        self.model_select.value_changed.connect(self.model_changed.emit)
        footer.addWidget(self.model_select)
        self.action_btn = compact_button(t("composer.send"), primary=True, width=84)
        self.action_btn.clicked.connect(self._on_action)
        footer.addWidget(self.action_btn)
        outer.addLayout(footer)
        # While running, the action button shows a spinning ring so it reads as a
        # live "stop" (icon, so the label never clips).
        self._busy = BusyAnimator(self._on_spin)

    def _apply_container_style(self) -> None:
        border = Theme.FOCUS if self._focused else Theme.BORDER
        self.setStyleSheet(
            f"QFrame#composer {{ background: {Theme.PANEL}; border: 1px solid {border};"
            f" border-radius: 12px; }}"
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
            options = [("No model", "default")]
        self.model_select.set_options(options)
        active = default or (options[0][1] if options else "default")
        self.model_select.set_value(active)

    def set_running(self, running: bool) -> None:
        from dbaide.i18n import t
        self._running = running
        if running:
            self.action_btn.setText(t("composer.stop"))
            self._busy.start()  # _on_spin paints the rotating ring icon
        else:
            self._busy.stop()
            self.action_btn.setIcon(QIcon())
            self.action_btn.setText(t("composer.send"))
        self.input.setEnabled(not running)
        self.policy_select.setEnabled(not running)
        self.model_select.setEnabled(not running)

    def _on_spin(self) -> None:
        self.action_btn.setIcon(spinner_icon(self._busy.angle, color="#ffffff"))

    def set_placeholder(self, text: str) -> None:
        self.input.setPlaceholderText(text)

    def set_disabled_no_connection(self, disabled: bool) -> None:
        self.input.setEnabled(not disabled)
        self.action_btn.setEnabled(not disabled)
        self.policy_select.setEnabled(not disabled)
        self.model_select.setEnabled(not disabled)

    def clear_input(self) -> None:
        self.input.clear()
        self._sync_input_height()

    def question(self) -> str:
        return self.input.toPlainText().strip()

    def mode(self) -> str:
        return "ask"

    def policy(self) -> str:
        return self.policy_select.value()

    def model_name(self) -> str:
        return self.model_select.value()

    def _on_action(self) -> None:
        # Decide by state, not button text (text is translated).
        if self._running:
            self.stop_requested.emit()
            return
        self.submit_requested.emit(self.question(), self.policy())
