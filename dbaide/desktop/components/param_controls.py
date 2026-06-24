"""Control bar for a parameterized dashboard — builds typed inputs from the
recipe's param schema and reports the current values on Apply.

text/number/date → line edit; enum → combo (or a row of checkboxes for
multi-select). Dynamic ``@`` defaults are resolved to concrete values for display.
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from dbaide.boards.dates import resolve_value
from dbaide.desktop.components.base import compact_button
from dbaide.desktop.components.inputs import STANDARD_FIELD_HEIGHT, configure_compact_field
from dbaide.desktop.theme import Theme
from dbaide.i18n import t as _t


class ParamControls(QWidget):
    applied = pyqtSignal(dict)   # {param_name: value}

    def __init__(self, controls: list[dict[str, Any]], defaults: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._getters: dict[str, Callable[[], Any]] = {}
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        for spec in controls:
            name = str(spec.get("name") or "")
            if not name:
                continue
            default = resolve_value(defaults.get(name, spec.get("default")))
            field = self._build_field(spec, default)
            cell = QVBoxLayout()
            cell.setSpacing(2)
            label = QLabel(str(spec.get("label") or name))
            label.setStyleSheet(f"color:{Theme.MUTED}; font-size:11px; background:transparent;")
            cell.addWidget(label)
            cell.addWidget(field)
            root.addLayout(cell)
        root.addStretch(1)
        apply_btn = compact_button(_t("app.apply"), primary=True, width=88)
        apply_btn.clicked.connect(lambda: self.applied.emit(self.values()))
        bottom = QVBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(apply_btn)
        root.addLayout(bottom)

    def values(self) -> dict[str, Any]:
        return {name: getter() for name, getter in self._getters.items()}

    # -- field builders -------------------------------------------------------

    def _build_field(self, spec: dict[str, Any], default: Any) -> QWidget:
        name = str(spec.get("name"))
        ptype = str(spec.get("type") or "text")
        options = spec.get("options") or []
        if ptype == "enum" and spec.get("multi"):
            return self._multi_enum(name, options, default)
        if ptype == "enum" and options:
            return self._single_enum(name, options, default)
        return self._line(name, default, placeholder=ptype)

    def _line(self, name: str, default: Any, placeholder: str) -> QWidget:
        edit = QLineEdit("" if default is None else str(default))
        edit.setPlaceholderText(placeholder)
        configure_compact_field(edit, height=STANDARD_FIELD_HEIGHT)
        edit.setMinimumWidth(120)
        self._getters[name] = lambda e=edit: (e.text().strip() or None)
        return edit

    def _single_enum(self, name: str, options: list, default: Any) -> QWidget:
        combo = QComboBox()
        for o in options:
            combo.addItem(str(o), o)
        if default is not None:
            i = combo.findData(default)
            if i >= 0:
                combo.setCurrentIndex(i)
        configure_compact_field(combo, height=STANDARD_FIELD_HEIGHT)
        combo.setMinimumWidth(120)
        self._getters[name] = lambda c=combo: c.currentData()
        return combo

    def _multi_enum(self, name: str, options: list, default: Any) -> QWidget:
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        chosen = set(default if isinstance(default, (list, tuple)) else ([] if default is None else [default]))
        boxes: list[tuple[QCheckBox, Any]] = []
        for o in options:
            cb = QCheckBox(str(o))
            cb.setChecked(o in chosen)
            cb.setStyleSheet(f"color:{Theme.TEXT}; background:transparent;")
            lay.addWidget(cb)
            boxes.append((cb, o))
        self._getters[name] = lambda b=boxes: [o for cb, o in b if cb.isChecked()]
        return host
