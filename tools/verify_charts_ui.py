#!/usr/bin/env python3
"""Render sample chart blocks offscreen and save PNGs for visual QA."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QWidget

from dbaide.desktop.components.chart_block import ChartBlock


def _grab(widget: QWidget, path: Path) -> None:
    widget.adjustSize()
    widget.resize(max(widget.sizeHint().width(), 640), widget.sizeHint().height())
    img = QImage(widget.size(), QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    widget.render(painter)
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))
    print(f"wrote {path}")


def main() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)

    out = ROOT / "docs" / "images" / "chart_verify"
    specs = {
        "combo_dual_axis.png": {
            "chart_id": "chart:1",
            "chart_type": "combo",
            "title": "销量与广告投入",
            "categories": ["06-01", "06-02", "06-03", "06-04"],
            "series": [
                {"name": "销量", "values": [120, 150, 132, 168], "type": "bar", "axis": "left", "unit": "单"},
                {"name": "广告投入", "values": [3500, 4200, 3900, 4500], "type": "line", "axis": "right", "unit": "元"},
            ],
            "axes": {
                "left": {"label": "销量", "format": "number"},
                "right": {"label": "广告投入", "format": "currency"},
            },
            "row_count": 4,
        },
        "stacked_area.png": {
            "chart_id": "chart:2",
            "chart_type": "stacked_area",
            "title": "渠道流量构成",
            "categories": ["Mon", "Tue", "Wed", "Thu"],
            "series": [
                {"name": "自然流量", "values": [10, 12, 13, 11], "type": "area"},
                {"name": "广告流量", "values": [4, 6, 8, 7], "type": "area"},
                {"name": "活动流量", "values": [2, 3, 2, 4], "type": "area"},
            ],
            "row_count": 4,
        },
        "multi_line_legend.png": {
            "chart_id": "chart:3",
            "chart_type": "line",
            "title": "各渠道 GMV 趋势",
            "categories": ["W1", "W2", "W3", "W4"],
            "series": [
                {"name": "天猫", "values": [120, 140, 135, 150], "type": "line"},
                {"name": "京东", "values": [80, 95, 88, 102], "type": "line"},
                {"name": "抖音", "values": [45, 60, 72, 85], "type": "line"},
            ],
            "row_count": 4,
        },
    }

    for name, spec in specs.items():
        block = ChartBlock(spec)
        _grab(block, out / name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
