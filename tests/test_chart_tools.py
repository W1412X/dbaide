from dbaide.agent.toolkit.chart_tools import _next_chart_id


class _State:
    charts = [{"chart_id": "chart:1"}]


def test_next_chart_id_increments():
    assert _next_chart_id(_State()) == "chart:2"
