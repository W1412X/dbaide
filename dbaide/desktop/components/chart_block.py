"""Web/ECharts conversation chart blocks."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from dbaide.charts.echarts import chart_spec_to_echarts_option, render_echarts_html
from dbaide.charts.labels import category_axis_layout
from dbaide.charts.spec import chart_spec_from_dict
from dbaide.desktop.theme import Theme
from dbaide.desktop.vendor_assets import echarts_script_src, webengine_html_base


def _chart_height(chart_type: str, category_count: int, categories: list[str] | None = None) -> int:
    cats = list(categories or [])
    _, angle, bottom_extra = category_axis_layout(cats) if cats else ("", 0, 0)
    if chart_type == "horizontal_bar":
        return min(560, max(240, 52 * max(category_count, 1) + 80))
    if chart_type in {"pie", "donut"}:
        return 360
    base = 280 + bottom_extra
    if angle:
        base += 16
    if category_count > 10:
        base += 24
    if category_count > 1:
        base += min(120, 18 * category_count)
    return min(560, max(320, base))


def _compact_axis_title(text: str) -> str:
    text = str(text or "").strip()
    for left, right in (("（", "）"), ("(", ")")):
        while left in text and right in text and text.index(left) < text.rindex(right):
            start = text.index(left)
            end = text.index(right, start)
            text = (text[:start] + text[end + 1:]).strip()
    if len(text) > 18:
        return text[:17].rstrip() + "…"
    return text


def _theme_payload() -> dict[str, Any]:
    return {
        "text": Theme.TEXT,
        "muted": Theme.MUTED,
        "border": Theme.BORDER_SOFT,
        "panel": Theme.BG,
        "colors": [
            Theme.ACCENT,
            Theme.GREEN,
            "#8b5cf6",
            Theme.BLUE,
            "#14b8a6",
            Theme.YELLOW,
            Theme.RED,
            "#f97316",
        ],
    }


def _echarts_src() -> str:
    return echarts_script_src()


def build_chart_widget(spec_dict: dict[str, Any]) -> QWidget:
    """Build a Qt WebEngine-hosted ECharts view from a serialized chart spec."""
    raw_series = [s for s in (spec_dict.get("series") or []) if isinstance(s, dict)]
    raw_categories = list(spec_dict.get("categories") or [])
    chart_type = str(spec_dict.get("chart_type") or "bar")
    has_values = any(isinstance(s.get("values"), list) and s.get("values") for s in raw_series)
    needs_categories = chart_type not in {"pie", "donut", "scatter"}
    if not raw_series or not has_values or (needs_categories and not raw_categories):
        from dbaide.i18n import t

        placeholder = QLabel(t("conversation.chart_no_data"))
        placeholder.setAlignment(QtAlignCenter())
        placeholder.setStyleSheet(f"color: {Theme.MUTED}; background: transparent; padding: 24px;")
        return placeholder

    spec = chart_spec_from_dict(spec_dict)
    echarts_src = _echarts_src()
    html = render_echarts_html(spec_dict, theme=_theme_payload(), echarts_src=echarts_src)
    base_url = webengine_html_base(echarts_src)
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "PyQt6-WebEngine is required for chart rendering. "
            "Install with: pip install PyQt6-WebEngine, then restart the app. "
            f"({exc})"
        ) from exc

    view = QWebEngineView()
    view.setHtml(html, base_url)
    try:
        from PyQt6.QtGui import QColor

        view.page().setBackgroundColor(QColor(Theme.BG))
    except Exception:
        pass
    height = _chart_height(spec.chart_type, len(spec.categories), list(spec.categories))
    view.setFixedHeight(height)
    view.setMinimumWidth(280)
    view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    view.setContextMenuPolicy(ContextMenuPolicyNoMenu())
    return view


def QtAlignCenter():
    from PyQt6.QtCore import Qt

    return Qt.AlignmentFlag.AlignCenter


def ContextMenuPolicyNoMenu():
    from PyQt6.QtCore import Qt

    return Qt.ContextMenuPolicy.NoContextMenu


class ChartBlock(QFrame):
    """Conversation block wrapping a Web/ECharts chart view."""

    def __init__(self, spec: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t

        self.setObjectName("chartBlock")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            f"""
            QFrame#chartBlock {{
                background: {Theme.PANEL};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 8px;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title = str(spec.get("title") or "").strip()
        if title:
            header = QLabel(title)
            header.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
            header.setStyleSheet(f"color: {Theme.TEXT}; background: transparent;")
            header.setWordWrap(True)
            layout.addWidget(header)

        row_count = int(spec.get("row_count") or 0)
        chart_type = str(spec.get("chart_type") or "bar")
        type_key = f"conversation.chart_type.{chart_type}"
        type_label = t(type_key)
        if type_label == type_key:
            type_label = t("conversation.chart")
        meta_parts = [type_label]
        if row_count:
            meta_parts.append(t("conversation.chart_points", n=row_count))
        series_count = len([s for s in (spec.get("series") or []) if isinstance(s, dict)])
        if series_count > 1:
            meta_parts.append(t("conversation.chart_series", n=series_count))
        axes = spec.get("axes") if isinstance(spec.get("axes"), dict) else {}
        right_axis = axes.get("right") if isinstance(axes, dict) else None
        right_label = str((right_axis or {}).get("label") or "").strip() if isinstance(right_axis, dict) else ""
        if right_label:
            meta_parts.append(t("conversation.chart_right_axis", label=_compact_axis_title(right_label)))
        meta = QLabel(" · ".join(meta_parts))
        meta.setStyleSheet(f"color: {Theme.MUTED}; background: transparent; font-size: 11px;")
        layout.addWidget(meta)

        try:
            layout.addWidget(build_chart_widget(spec))
        except Exception as exc:  # noqa: BLE001
            err = QLabel(str(exc))
            err.setWordWrap(True)
            err.setStyleSheet(f"color: {Theme.RED}; background: transparent;")
            layout.addWidget(err)

    def _viewport_width(self) -> int:
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QScrollArea):
                return w.viewport().width()
            w = w.parentWidget()
        return 0

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        vw = self._viewport_width()
        if vw > 0:
            # Keep chart inside the conversation column; inner WebEngine resizes via ResizeObserver.
            self.setMaximumWidth(max(280, vw - 4))


__all__ = ["ChartBlock", "build_chart_widget", "chart_spec_to_echarts_option"]
