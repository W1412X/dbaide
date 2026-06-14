from dbaide.charts.spec import ChartSpec, chart_spec_from_dict, chart_spec_to_dict


def test_chart_spec_roundtrip():
    spec = ChartSpec(
        chart_id="chart:1",
        chart_type="horizontal_bar",
        title="Factory power",
        categories=["A", "B"],
        series=[{"name": "kW", "values": [10.0, 20.0]}],
        x_label="kW",
        y_label="Factory",
        row_count=2,
        axes={"left": {"label": "kW", "format": "number"}},
    )
    payload = chart_spec_to_dict(spec)
    restored = chart_spec_from_dict(payload)
    assert restored.chart_id == "chart:1"
    assert restored.chart_type == "horizontal_bar"
    assert restored.categories == ["A", "B"]
    assert restored.series[0]["values"] == [10.0, 20.0]
    assert restored.axes["left"]["label"] == "kW"


def test_chart_spec_accepts_combo_series_metadata():
    spec = ChartSpec(
        chart_id="chart:2",
        chart_type="combo",
        title="Sales and spend",
        categories=["2026-06-01", "2026-06-02"],
        series=[
            {"name": "Sales", "values": [10, 12], "type": "bar", "axis": "left"},
            {"name": "Spend", "values": [100, 150], "type": "line", "axis": "right"},
        ],
        axes={"left": {"label": "Sales"}, "right": {"label": "Spend"}},
    )
    payload = chart_spec_to_dict(spec)
    restored = chart_spec_from_dict(payload)
    assert restored.series[1]["type"] == "line"
    assert restored.axes["right"]["label"] == "Spend"
