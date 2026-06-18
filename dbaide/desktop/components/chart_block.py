"""Qt Charts widget for conversation chart blocks."""

from __future__ import annotations

import math
from typing import Any

from PyQt6.QtCore import QSize, Qt, QMargins
from PyQt6.QtGui import QBrush, QColor, QCursor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from dbaide.charts.labels import category_axis_layout
from dbaide.charts.spec import chart_spec_from_dict
from dbaide.desktop.theme import Theme


def _hex_color(token: str, fallback: str = "#3b82f6") -> QColor:
    raw = str(getattr(Theme, token, "") or fallback)
    return QColor(raw)


def _series_colors() -> list[QColor]:
    return [
        _hex_color("ACCENT"),
        _hex_color("GREEN"),
        QColor("#8b5cf6"),
        _hex_color("BLUE"),
        QColor("#14b8a6"),
        _hex_color("YELLOW"),
        _hex_color("RED"),
        QColor("#f97316"),
    ]


def _safe_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _series_values(item: dict[str, Any], count: int = 0) -> list[float]:
    values = [_safe_float(v) for v in (item.get("values") or [])]
    if count > 0:
        values = values[:count]
        if len(values) < count:
            values.extend([0.0] * (count - len(values)))
    return values


def _format_tooltip_value(value: object) -> str:
    if not isinstance(value, (int, float)):
        return str(value)
    v = float(value)
    if not math.isfinite(v):
        return str(value)
    if abs(v) < 1e-8:
        return "0"
    if abs(v - round(v)) < 1e-6 and abs(v) < 1e15:
        return f"{int(round(v)):,}"
    if abs(v) >= 1000:
        return f"{v:,.1f}"
    return f"{v:.2f}"


def _truncate_label(text: str, max_len: int = 20) -> str:
    """Pie slice labels only — axis categories use charts.labels instead."""
    text = " ".join(str(text or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _style_value_axis(axis, values: list[float] | None = None, *, value_format: str = "") -> None:
    axis.setLabelsColor(_hex_color("MUTED"))
    axis.setTitleBrush(_hex_color("TEXT_2"))
    axis.setTitleFont(QFont("Inter", 10))
    axis.setLabelsFont(QFont("Inter", 9))
    axis.setGridLineColor(_hex_color("BORDER_SOFT"))
    axis.setMinorGridLineVisible(False)
    axis.setGridLineVisible(True)
    axis.setLinePenColor(_hex_color("BORDER"))
    if values:
        lo = min(0.0, min(values))
        hi = max(values) if values else 1.0
        if hi <= lo:
            hi = lo + 1.0
        pad = (hi - lo) * 0.10 or hi * 0.10 or 1.0
        axis.setRange(lo, hi + pad)
    axis.setTickCount(min(6, max(3, 4)))
    fmt = str(value_format or "").strip().lower()
    if fmt == "percent":
        axis.setLabelFormat("%.1f%%")
    elif fmt == "currency":
        axis.setLabelFormat("%.0f")
    elif values and all(abs(v - round(v)) < 1e-6 for v in values if v is not None):
        axis.setLabelFormat("%.0f")
    else:
        axis.setLabelFormat("%.1f")


def _style_category_axis(axis, *, labels_angle: int = 0) -> None:
    axis.setLabelsColor(_hex_color("TEXT_2"))
    font_size = 9 if labels_angle else 10
    axis.setLabelsFont(QFont("Inter", font_size))
    axis.setTitleFont(QFont("Inter", 10))
    axis.setTitleBrush(_hex_color("TEXT_2"))
    axis.setGridLineVisible(False)
    axis.setLinePenColor(_hex_color("BORDER"))
    axis.setTruncateLabels(False)
    if labels_angle:
        axis.setLabelsAngle(labels_angle)


def _configure_category_axis(axis, raw_categories: list[str], *, title: str = "") -> int:
    """Append display labels; return extra bottom margin for rotated text."""
    display, angle, bottom_extra = category_axis_layout(raw_categories)
    axis.append(display)
    _style_category_axis(axis, labels_angle=angle)
    if title:
        axis.setTitleText(_compact_axis_title(title))
    return bottom_extra


def _style_bar_set(bar_set, color: QColor) -> None:
    bar_set.setColor(color)
    border = QColor(color)
    border.setAlpha(0)
    bar_set.setBorderColor(border)


def _apply_chart_chrome(chart, *, left_margin: int = 4, bottom_margin: int = 8, show_legend: bool = False) -> None:
    from PyQt6.QtCharts import QChart

    chart.setBackgroundVisible(False)
    chart.setPlotAreaBackgroundVisible(True)
    chart.setPlotAreaBackgroundBrush(QBrush(_hex_color("PANEL_2")))
    chart.setPlotAreaBackgroundPen(QPen(_hex_color("BORDER_SOFT")))
    chart.setTitle("")
    chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
    chart.setMargins(QMargins(left_margin, 8, 8, bottom_margin))
    legend = chart.legend()
    legend.setVisible(bool(show_legend))
    legend.setAlignment(Qt.AlignmentFlag.AlignBottom)
    legend.setLabelColor(_hex_color("TEXT_2"))
    legend.setFont(QFont("Inter", 10))
    legend.setBackgroundVisible(False)


class _ChartView(QWidget):
    """QChartView wrapper with hover tooltips (full category + value on axis charts)."""

    def __init__(
        self,
        chart,
        *,
        categories: list[str],
        chart_type: str = "",
        series_units: dict[str, str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        from PyQt6.QtCharts import (
            QBarSeries,
            QChartView,
            QHorizontalBarSeries,
            QLineSeries,
            QStackedBarSeries,
        )

        self._categories = list(categories)
        self._series_units = dict(series_units or {})
        self._view = QChartView(chart, self)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._view.setStyleSheet("background: transparent; border: none;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        for series in chart.series():
            if isinstance(series, QHorizontalBarSeries):
                series.setBarWidth(0.72)
                series.hovered.connect(self._on_bar_hovered)
            elif isinstance(series, (QBarSeries, QStackedBarSeries)):
                series.hovered.connect(self._on_bar_hovered)
            elif isinstance(series, QLineSeries):
                name = series.name()
                series.hovered.connect(
                    lambda point, status, n=name: self._on_line_hovered(point, status, n)
                )

    def _show_tooltip(self, index: int, value: object, *, series_name: str = "") -> None:
        if index < 0 or index >= len(self._categories):
            QToolTip.hideText()
            return
        cat = self._categories[index]
        unit = self._series_units.get(series_name, "")
        val = f"{_format_tooltip_value(value)}{unit}"
        text = f"{series_name}\n{cat}\n{val}" if series_name else f"{cat}\n{val}"
        QToolTip.showText(QCursor.pos(), text)

    def _on_bar_hovered(self, status: bool, index: int, barset) -> None:
        if not status:
            QToolTip.hideText()
            return
        try:
            val = barset.at(index)
        except Exception:
            val = "?"
        self._show_tooltip(index, val, series_name=str(barset.label() or ""))

    def _on_line_hovered(self, point, status: bool, series_name: str = "") -> None:
        if not status:
            QToolTip.hideText()
            return
        index = int(round(point.x()))
        self._show_tooltip(index, point.y(), series_name=series_name)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._view.setGeometry(0, 0, self.width(), self.height())


def _chart_height(chart_type: str, category_count: int, *, bottom_extra: int = 0) -> int:
    if chart_type == "horizontal_bar":
        return min(560, max(240, 52 * max(category_count, 1) + 80))
    if chart_type in ("pie", "donut"):
        return 340
    base = min(440, max(280, 36 * max(category_count, 1) + 120))
    return base + bottom_extra


def _series_kind(item: dict[str, Any], chart_type: str, index: int) -> str:
    raw = str(item.get("type") or "").strip().lower()
    if raw in {"bar", "line", "area"}:
        return raw
    if chart_type in {"line", "multi_axis_line"}:
        return "line"
    if chart_type in {"area", "stacked_area"}:
        return "area"
    if chart_type == "combo":
        return "bar" if index == 0 else "line"
    return "bar"


def _series_axis(item: dict[str, Any]) -> str:
    return "right" if str(item.get("axis") or "").strip().lower() == "right" else "left"


def _axis_config(spec, side: str) -> dict[str, Any]:
    axes = spec.axes or {}
    raw = axes.get(side) if isinstance(axes, dict) else None
    return dict(raw or {}) if isinstance(raw, dict) else {}


def _axis_label(spec, side: str) -> str:
    cfg = _axis_config(spec, side)
    label = str(cfg.get("label") or "").strip()
    if label:
        return label
    return spec.y_label if side == "left" else ""


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


def _axis_format(spec, side: str) -> str:
    return str(_axis_config(spec, side).get("format") or "").strip()


def _series_units(spec) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in spec.series:
        name = str(item.get("name") or "")
        unit = str(item.get("unit") or "").strip()
        if name and unit:
            out[name] = unit
    return out


def _wrap_chart(chart, *, categories: list[str], chart_type: str, height: int, series_units: dict[str, str] | None = None) -> QWidget:
    view = _ChartView(chart, categories=categories, chart_type=chart_type, series_units=series_units)
    view.setMinimumHeight(height)
    view.setMaximumHeight(height + 40)
    view.setMinimumWidth(320)
    view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return view


def build_chart_widget(spec_dict: dict[str, Any]) -> QWidget:
    """Build a styled QChartView from a serialized chart spec."""
    # A no-data spec (0-row query → series with empty values and/or no categories)
    # would make chart_spec_from_dict raise a raw validation ValueError ("each series
    # requires non-empty values") that ChartBlock surfaces verbatim to the user. Detect
    # it up front and show a clean placeholder instead. Genuine spec errors (bad
    # chart_type / series type / axis) still propagate. Done before importing QtCharts —
    # an empty chart needs only a QLabel.
    _raw_series = [s for s in (spec_dict.get("series") or []) if isinstance(s, dict)]
    _raw_categories = list(spec_dict.get("categories") or [])
    _chart_type = str(spec_dict.get("chart_type") or "bar")
    _has_values = any(
        isinstance(s.get("values"), list) and s.get("values") for s in _raw_series
    )
    _needs_categories = _chart_type not in ("pie", "donut", "scatter")
    if not _raw_series or not _has_values or (_needs_categories and not _raw_categories):
        from dbaide.i18n import t
        placeholder = QLabel(t("conversation.chart_no_data"))
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet(f"color: {_hex_color('MUTED').name()}; background: transparent; padding: 24px;")
        return placeholder

    spec = chart_spec_from_dict(spec_dict)
    raw_categories = [str(c) for c in spec.categories]

    from PyQt6.QtCharts import (
        QBarCategoryAxis,
        QBarSeries,
        QBarSet,
        QChart,
        QHorizontalBarSeries,
        QLineSeries,
        QPieSeries,
        QScatterSeries,
        QStackedBarSeries,
        QValueAxis,
    )

    chart = QChart()
    colors = _series_colors()
    chart_type = spec.chart_type
    _, _angle, bottom_extra = category_axis_layout(raw_categories)
    show_legend = len(spec.series) > 1 or chart_type in {"pie", "donut"}
    units = _series_units(spec)

    if chart_type in ("pie", "donut"):
        pie = QPieSeries()
        values = _series_values(spec.series[0], len(raw_categories))
        total = sum(abs(v) for v in values) or 1.0
        n_cats = len(raw_categories)
        for idx, (cat, val) in enumerate(zip(raw_categories, values, strict=False)):
            slice_ = pie.append(_truncate_label(cat, 20), val)
            if n_cats <= 6:
                slice_.setLabelVisible(True)
            elif n_cats <= 10:
                slice_.setLabelVisible(abs(val) / total >= 0.05)
            else:
                slice_.setLabelVisible(abs(val) / total >= 0.08)
            slice_.setLabelColor(_hex_color("TEXT_2"))
            slice_.setColor(colors[idx % len(colors)])
            slice_.setBorderColor(_hex_color("PANEL"))
        if chart_type == "donut":
            pie.setHoleSize(0.45)
        chart.addSeries(pie)
        _apply_chart_chrome(chart, bottom_margin=8 + bottom_extra, show_legend=show_legend)
    elif chart_type == "scatter":
        item = spec.series[0]
        ys = _series_values(item, len(raw_categories))
        scatter = QScatterSeries()
        scatter.setName(str(item.get("name") or "values"))
        scatter.setColor(_hex_color("ACCENT"))
        scatter.setMarkerSize(9.0)
        scatter.setBorderColor(_hex_color("ACCENT"))
        for x_raw, y_raw in zip(raw_categories, ys, strict=False):
            try:
                x_val = float(x_raw)
                if not math.isfinite(x_val):
                    raise ValueError
            except (TypeError, ValueError):
                x_val = float(scatter.count())
            scatter.append(x_val, _safe_float(y_raw))
        chart.addSeries(scatter)
        all_y = list(ys)
        axis_x = QValueAxis()
        axis_x.setTitleText(_compact_axis_title(spec.x_label))
        _style_value_axis(axis_x)
        axis_y = QValueAxis()
        axis_y.setTitleText(_compact_axis_title(_axis_label(spec, "left") or spec.y_label))
        _style_value_axis(axis_y, all_y, value_format=_axis_format(spec, "left"))
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        scatter.attachAxis(axis_x)
        scatter.attachAxis(axis_y)
        _apply_chart_chrome(chart, bottom_margin=8 + bottom_extra, show_legend=show_legend)
    elif chart_type == "horizontal_bar":
        all_values: list[float] = []
        series = QHorizontalBarSeries()
        for idx, item in enumerate(spec.series):
            bar_set = QBarSet(str(item.get("name") or ""))
            vals = _series_values(item, len(raw_categories))
            all_values.extend(vals)
            for val in vals:
                bar_set.append(val)
            _style_bar_set(bar_set, colors[idx % len(colors)])
            series.append(bar_set)
        if len(spec.series) == 1:
            series.setName("")
        chart.addSeries(series)
        axis_y = QBarCategoryAxis()
        _configure_category_axis(axis_y, raw_categories)
        axis_x = QValueAxis()
        axis_x.setTitleText(_compact_axis_title(spec.x_label or _axis_label(spec, "left")))
        _style_value_axis(axis_x, all_values, value_format=_axis_format(spec, "left"))
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_y)
        series.attachAxis(axis_x)
        max_cat_len = max((len(c) for c in raw_categories), default=0)
        h_left = min(100, max(4, max_cat_len * 6))
        _apply_chart_chrome(chart, left_margin=h_left, bottom_margin=8 + bottom_extra, show_legend=show_legend)
        return _wrap_chart(
            chart,
            categories=raw_categories,
            chart_type=chart_type,
            height=_chart_height(chart_type, len(raw_categories)),
            series_units=units,
        )
    elif chart_type == "stacked_area":
        all_y: list[float] = []
        bar_series = QStackedBarSeries()
        for idx, item in enumerate(spec.series):
            bar_set = QBarSet(str(item.get("name") or f"Series {idx + 1}"))
            vals = _series_values(item, len(raw_categories))
            all_y.extend(vals)
            for val in vals:
                bar_set.append(val)
            _style_bar_set(bar_set, colors[idx % len(colors)])
            bar_series.append(bar_set)
        chart.addSeries(bar_series)
        axis_x = QBarCategoryAxis()
        _configure_category_axis(axis_x, raw_categories, title=spec.x_label)
        axis_y = QValueAxis()
        axis_y.setTitleText(_compact_axis_title(_axis_label(spec, "left") or spec.y_label))
        _style_value_axis(axis_y, all_y, value_format=_axis_format(spec, "left"))
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        bar_series.attachAxis(axis_x)
        bar_series.attachAxis(axis_y)
        _apply_chart_chrome(chart, bottom_margin=8 + bottom_extra, show_legend=show_legend)
        h = _chart_height(chart_type, len(raw_categories), bottom_extra=bottom_extra)
        return _wrap_chart(chart, categories=raw_categories, chart_type=chart_type, height=h, series_units=units)
    elif chart_type in {"line", "area", "combo", "multi_axis_line"}:
        axis_x = QBarCategoryAxis()
        _configure_category_axis(axis_x, raw_categories, title=spec.x_label)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)

        left_values: list[float] = []
        right_values: list[float] = []
        attached: list[tuple[object, str]] = []
        left_bar_series = QBarSeries()
        right_bar_series = QBarSeries()

        for idx, item in enumerate(spec.series):
            name = str(item.get("name") or f"Series {idx + 1}")
            vals = _series_values(item, len(raw_categories))
            axis_side = _series_axis(item)
            kind = _series_kind(item, chart_type, idx)
            color = colors[idx % len(colors)]

            if kind == "bar":
                bar_set = QBarSet(name)
                for val in vals:
                    bar_set.append(val)
                _style_bar_set(bar_set, color)
                if axis_side == "right":
                    right_bar_series.append(bar_set)
                    right_values.extend(vals)
                else:
                    left_bar_series.append(bar_set)
                    left_values.extend(vals)
                continue

            if kind == "area":
                line = QLineSeries()
                line.setName(name)
                line.setColor(color)
                pen = line.pen()
                pen.setWidthF(2.8)
                line.setPen(pen)
                for cat_idx, val in enumerate(vals):
                    line.append(float(cat_idx), val)
                target_values = right_values if axis_side == "right" else left_values
                target_values.extend(vals)
                chart.addSeries(line)
                attached.append((line, axis_side))
                continue

            line = QLineSeries()
            line.setName(name)
            line.setColor(color)
            pen = line.pen()
            pen.setWidthF(2.4)
            line.setPen(pen)
            for cat_idx, val in enumerate(vals):
                line.append(float(cat_idx), val)
            target_values = right_values if axis_side == "right" else left_values
            target_values.extend(vals)
            chart.addSeries(line)
            attached.append((line, axis_side))

        if left_bar_series.count() > 0:
            chart.addSeries(left_bar_series)
            attached.append((left_bar_series, "left"))
        if right_bar_series.count() > 0:
            chart.addSeries(right_bar_series)
            attached.append((right_bar_series, "right"))

        axis_left = None
        axis_right = None
        if left_values:
            axis_left = QValueAxis()
            axis_left.setTitleText(_compact_axis_title(_axis_label(spec, "left") or spec.y_label))
            _style_value_axis(axis_left, left_values, value_format=_axis_format(spec, "left"))
            chart.addAxis(axis_left, Qt.AlignmentFlag.AlignLeft)
        if right_values:
            axis_right = QValueAxis()
            axis_right.setTitleText(_compact_axis_title(_axis_label(spec, "right")))
            _style_value_axis(axis_right, right_values, value_format=_axis_format(spec, "right"))
            chart.addAxis(axis_right, Qt.AlignmentFlag.AlignRight)
        for series_obj, side in attached:
            series_obj.attachAxis(axis_x)
            if side == "right" and axis_right is not None:
                series_obj.attachAxis(axis_right)
            elif axis_left is not None:
                series_obj.attachAxis(axis_left)
        _apply_chart_chrome(chart, bottom_margin=8 + bottom_extra, show_legend=show_legend)
        h = _chart_height(chart_type, len(raw_categories), bottom_extra=bottom_extra)
        return _wrap_chart(chart, categories=raw_categories, chart_type=chart_type, height=h, series_units=units)
    else:
        all_y = []
        if chart_type == "stacked_bar":
            bar_series: QBarSeries | QStackedBarSeries = QStackedBarSeries()
        else:
            bar_series = QBarSeries()
        for idx, item in enumerate(spec.series):
            bar_set = QBarSet(str(item.get("name") or f"Series {idx + 1}"))
            vals = _series_values(item, len(raw_categories))
            all_y.extend(vals)
            for val in vals:
                bar_set.append(val)
            _style_bar_set(bar_set, colors[idx % len(colors)])
            bar_series.append(bar_set)
        chart.addSeries(bar_series)
        axis_x = QBarCategoryAxis()
        _configure_category_axis(axis_x, raw_categories, title=spec.x_label)
        axis_y = QValueAxis()
        axis_y.setTitleText(_compact_axis_title(_axis_label(spec, "left") or spec.y_label))
        _style_value_axis(axis_y, all_y, value_format=_axis_format(spec, "left"))
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        bar_series.attachAxis(axis_x)
        bar_series.attachAxis(axis_y)
        _apply_chart_chrome(chart, bottom_margin=8 + bottom_extra, show_legend=show_legend)
        h = _chart_height(chart_type, len(raw_categories), bottom_extra=bottom_extra)
        return _wrap_chart(chart, categories=raw_categories, chart_type=chart_type, height=h, series_units=units)

    h = _chart_height(chart_type, len(raw_categories), bottom_extra=bottom_extra)
    return _wrap_chart(chart, categories=raw_categories, chart_type=chart_type, height=h, series_units=units)


class ChartBlock(QFrame):
    """Conversation block wrapping a Qt Charts view."""

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
        except Exception as exc:
            err = QLabel(str(exc))
            err.setWordWrap(True)
            err.setStyleSheet(f"color: {Theme.RED}; background: transparent;")
            layout.addWidget(err)

    def _viewport_width(self) -> int:
        """Walk up the widget tree to find the enclosing QScrollArea viewport width."""
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
            self.setMaximumWidth(vw)
