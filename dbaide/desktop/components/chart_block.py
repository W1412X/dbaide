"""Qt Charts widget for conversation chart blocks."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QMargins
from PyQt6.QtGui import QBrush, QColor, QCursor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QToolTip, QVBoxLayout, QWidget

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


def _truncate_label(text: str, max_len: int = 16) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _style_value_axis(axis, values: list[float] | None = None) -> None:
    axis.setLabelsColor(_hex_color("MUTED"))
    axis.setTitleBrush(_hex_color("TEXT_2"))
    axis.setTitleFont(QFont("Inter", 10))
    axis.setLabelsFont(QFont("Inter", 10))
    axis.setGridLineColor(_hex_color("BORDER_SOFT"))
    axis.setMinorGridLineVisible(False)
    axis.setGridLineVisible(True)
    axis.setLinePenColor(_hex_color("BORDER"))
    if values:
        lo = min(0.0, min(values))
        hi = max(values) if values else 1.0
        if hi <= lo:
            hi = lo + 1.0
        pad = (hi - lo) * 0.08 or hi * 0.08 or 1.0
        axis.setRange(lo, hi + pad)
    axis.setTickCount(min(6, max(3, 4)))
    axis.setLabelFormat("")  # use callback below when supported
    # PyQt6 QValueAxis: setLabelFormat("%.0f") for integer-ish scales
    if values and all(abs(v - round(v)) < 1e-6 for v in values if v is not None):
        axis.setLabelFormat("%.0f")
    else:
        axis.setLabelFormat("%.1f")


def _style_category_axis(axis) -> None:
    axis.setLabelsColor(_hex_color("TEXT_2"))
    axis.setLabelsFont(QFont("Inter", 10))
    axis.setGridLineVisible(False)
    axis.setLinePenColor(_hex_color("BORDER"))


def _style_bar_set(bar_set, color: QColor) -> None:
    bar_set.setColor(color)
    border = QColor(color)
    border.setAlpha(0)
    bar_set.setBorderColor(border)


def _apply_chart_chrome(chart) -> None:
    from PyQt6.QtCharts import QChart

    chart.setBackgroundVisible(False)
    chart.setPlotAreaBackgroundVisible(True)
    chart.setPlotAreaBackgroundBrush(QBrush(_hex_color("PANEL_2")))
    chart.setPlotAreaBackgroundPen(QPen(_hex_color("BORDER_SOFT")))
    chart.setTitle("")
    chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
    chart.setMargins(QMargins(8, 8, 12, 8))
    legend = chart.legend()
    legend.setVisible(False)
    legend.setLabelColor(_hex_color("TEXT_2"))
    legend.setFont(QFont("Inter", 10))
    legend.setBackgroundVisible(False)


class _ChartView(QWidget):
    """QChartView wrapper with bar hover tooltips (full category + value)."""

    def __init__(
        self,
        chart,
        *,
        categories: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        from PyQt6.QtCharts import QChartView, QHorizontalBarSeries

        self._categories = list(categories)
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

    def _on_bar_hovered(self, status: bool, index: int, barset) -> None:
        if not status or index < 0 or index >= len(self._categories):
            QToolTip.hideText()
            return
        cat = self._categories[index]
        try:
            val = barset.at(index)
        except Exception:
            val = "?"
        QToolTip.showText(QCursor.pos(), f"{cat}\n{val:,.2g}" if isinstance(val, (int, float)) else f"{cat}\n{val}")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._view.setGeometry(0, 0, self.width(), self.height())


def _chart_height(chart_type: str, category_count: int) -> int:
    if chart_type == "horizontal_bar":
        return min(520, max(220, 52 * max(category_count, 1) + 72))
    if chart_type in ("pie", "donut"):
        return 320
    return min(420, max(260, 36 * max(category_count, 1) + 120))


def build_chart_widget(spec_dict: dict[str, Any]) -> QWidget:
    """Build a styled QChartView from a serialized chart spec."""
    from PyQt6.QtCharts import (
        QBarCategoryAxis,
        QBarSeries,
        QBarSet,
        QChart,
        QChartView,
        QHorizontalBarSeries,
        QLineSeries,
        QPieSeries,
        QScatterSeries,
        QStackedBarSeries,
        QValueAxis,
    )

    spec = chart_spec_from_dict(spec_dict)
    chart = QChart()
    _apply_chart_chrome(chart)
    if len(spec.series) > 1:
        chart.legend().setVisible(True)

    colors = _series_colors()
    raw_categories = [str(c) for c in spec.categories]
    display_categories = [_truncate_label(c) for c in raw_categories]
    chart_type = spec.chart_type

    if chart_type in ("pie", "donut"):
        pie = QPieSeries()
        values = spec.series[0].get("values") or []
        for idx, (cat, val) in enumerate(zip(raw_categories, values, strict=False)):
            slice_ = pie.append(_truncate_label(cat, 20), float(val))
            slice_.setLabelVisible(len(raw_categories) <= 6)
            slice_.setLabelColor(_hex_color("TEXT_2"))
            slice_.setColor(colors[idx % len(colors)])
            slice_.setBorderColor(_hex_color("PANEL"))
        if chart_type == "donut":
            pie.setHoleSize(0.45)
        chart.addSeries(pie)
    elif chart_type == "scatter":
        item = spec.series[0]
        ys = item.get("values") or []
        scatter = QScatterSeries()
        scatter.setName(str(item.get("name") or "values"))
        scatter.setColor(_hex_color("ACCENT"))
        scatter.setMarkerSize(9.0)
        scatter.setBorderColor(_hex_color("ACCENT"))
        for x_raw, y_raw in zip(raw_categories, ys, strict=False):
            try:
                x_val = float(x_raw)
            except (TypeError, ValueError):
                x_val = float(scatter.count())
            scatter.append(x_val, float(y_raw))
        chart.addSeries(scatter)
        all_y = [float(y) for y in ys]
        axis_x = QValueAxis()
        axis_x.setTitleText(spec.x_label)
        _style_value_axis(axis_x)
        axis_y = QValueAxis()
        axis_y.setTitleText(spec.y_label)
        _style_value_axis(axis_y, all_y)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        scatter.attachAxis(axis_x)
        scatter.attachAxis(axis_y)
    elif chart_type == "horizontal_bar":
        all_values: list[float] = []
        series = QHorizontalBarSeries()
        for idx, item in enumerate(spec.series):
            bar_set = QBarSet(str(item.get("name") or ""))
            vals = [float(v) for v in (item.get("values") or [])]
            all_values.extend(vals)
            for val in vals:
                bar_set.append(val)
            _style_bar_set(bar_set, colors[idx % len(colors)])
            series.append(bar_set)
        if len(spec.series) == 1:
            series.setName("")
        chart.addSeries(series)
        axis_y = QBarCategoryAxis()
        axis_y.append(display_categories)
        _style_category_axis(axis_y)
        axis_x = QValueAxis()
        axis_x.setTitleText(spec.x_label)
        _style_value_axis(axis_x, all_values)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_y)
        series.attachAxis(axis_x)
        wrapper = _ChartView(chart, categories=raw_categories)
        wrapper.setMinimumHeight(_chart_height(chart_type, len(raw_categories)))
        wrapper.setMaximumHeight(_chart_height(chart_type, len(raw_categories)) + 40)
        wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return wrapper
    elif chart_type in ("line", "area"):
        lines: list[QLineSeries] = []
        all_y: list[float] = []
        for idx, item in enumerate(spec.series):
            line = QLineSeries()
            line.setName(str(item.get("name") or f"Series {idx + 1}"))
            color = colors[idx % len(colors)]
            line.setColor(color)
            pen = line.pen()
            pen.setWidthF(2.4 if chart_type == "line" else 2.0)
            line.setPen(pen)
            vals = item.get("values") or []
            all_y.extend(float(v) for v in vals)
            for cat_idx, val in enumerate(vals):
                line.append(float(cat_idx), float(val))
            chart.addSeries(line)
            lines.append(line)
        axis_x = QBarCategoryAxis()
        axis_x.append(display_categories)
        _style_category_axis(axis_x)
        axis_y = QValueAxis()
        axis_y.setTitleText(spec.y_label)
        _style_value_axis(axis_y, all_y)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        for line in lines:
            line.attachAxis(axis_x)
            line.attachAxis(axis_y)
    else:
        all_y = []
        if chart_type == "stacked_bar":
            bar_series: QBarSeries | QStackedBarSeries = QStackedBarSeries()
        else:
            bar_series = QBarSeries()
        for idx, item in enumerate(spec.series):
            bar_set = QBarSet(str(item.get("name") or f"Series {idx + 1}"))
            vals = [float(v) for v in (item.get("values") or [])]
            all_y.extend(vals)
            for val in vals:
                bar_set.append(val)
            _style_bar_set(bar_set, colors[idx % len(colors)])
            bar_series.append(bar_set)
        chart.addSeries(bar_series)
        axis_x = QBarCategoryAxis()
        axis_x.append(display_categories)
        _style_category_axis(axis_x)
        axis_y = QValueAxis()
        axis_y.setTitleText(spec.y_label)
        _style_value_axis(axis_y, all_y)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        bar_series.attachAxis(axis_x)
        bar_series.attachAxis(axis_y)

    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    view.setMinimumHeight(_chart_height(chart_type, len(raw_categories)))
    view.setMaximumHeight(_chart_height(chart_type, len(raw_categories)) + 40)
    view.setStyleSheet("background: transparent; border: none;")
    return view


class ChartBlock(QFrame):
    """Conversation block wrapping a Qt Charts view."""

    def __init__(self, spec: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        from dbaide.i18n import t

        self.setObjectName("chartBlock")
        self.setStyleSheet(
            f"""
            QFrame#chartBlock {{
                background: {Theme.PANEL};
                border: 1px solid {Theme.BORDER_SOFT};
                border-radius: 12px;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = str(spec.get("title") or "").strip()
        if title:
            header = QLabel(title)
            header.setFont(QFont("Inter", 14, QFont.Weight.DemiBold))
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
