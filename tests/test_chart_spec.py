from dbaide.charts.spec import ChartOptions, ChartSpec, chart_spec_from_dict, chart_spec_to_dict


def test_chart_spec_scatter_accepts_points_payload_without_categories():
    spec = ChartSpec(
        chart_id="chart:scatter",
        chart_type="scatter",
        title="Scatter",
        series=[],
        data={"points": [{"name": "A", "x": 1.0, "y": 2.0}]},
    )
    spec.validate()
    restored = chart_spec_from_dict(chart_spec_to_dict(spec))
    assert restored.data["points"][0]["x"] == 1.0


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


def test_chart_spec_roundtrip_preserves_options_and_special_data():
    spec = ChartSpec(
        chart_id="chart:3",
        chart_type="radar",
        title="Capability",
        options=ChartOptions(smooth=False, show_labels=True, legend_position="top", radar_shape="circle"),
        data={
            "indicators": [{"name": "A", "max": 10}],
            "radar_series": [{"name": "team-1", "value": [7]}],
        },
    )
    payload = chart_spec_to_dict(spec)
    restored = chart_spec_from_dict(payload)
    assert restored.options.show_labels is True
    assert restored.options.legend_position == "top"
    assert restored.data["indicators"][0]["name"] == "A"
