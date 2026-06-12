from dbaide.agent.chart_agent import ChartAgent, _heuristic_plan
from dbaide.charts.spec import chart_spec_to_dict


def test_heuristic_plan_prefers_horizontal_bar_for_text_categories():
    rows = [
        {"factory": "快讯网络有限公司制造基地", "power": 4540.1},
        {"factory": "毕博诚科技有限公司制造基地", "power": 4406.0},
    ]
    plan = _heuristic_plan(["factory", "power"], rows, intent="功率对比")
    assert plan.category_field == "factory"
    assert plan.value_fields == ["power"]
    spec = ChartAgent().build_spec(plan, chart_id="chart:1", rows=rows)
    payload = chart_spec_to_dict(spec)
    assert payload["categories"][0].startswith("快讯")
    assert payload["series"][0]["values"][0] == 4540.1
