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
    )
    payload = chart_spec_to_dict(spec)
    restored = chart_spec_from_dict(payload)
    assert restored.chart_id == "chart:1"
    assert restored.chart_type == "horizontal_bar"
    assert restored.categories == ["A", "B"]
    assert restored.series[0]["values"] == [10.0, 20.0]
