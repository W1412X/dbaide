import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_chart_block_builds_qt_chart(qapp):
    pytest.importorskip("PyQt6.QtCharts")
    from dbaide.desktop.components.chart_block import ChartBlock

    spec = {
        "chart_id": "chart:1",
        "chart_type": "horizontal_bar",
        "title": "Factory power",
        "categories": ["A", "B"],
        "series": [{"name": "kW", "values": [10.0, 20.0]}],
        "row_count": 2,
    }
    block = ChartBlock(spec)
    assert block.layout().count() >= 2
