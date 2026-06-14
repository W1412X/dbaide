from dbaide.charts.embed import chart_embed_markdown, normalize_chart_id, split_answer_with_charts


def _spec(n: int) -> dict:
    return {"chart_id": f"chart:{n}", "chart_type": "bar", "title": f"T{n}"}


def test_chart_embed_markdown():
    assert chart_embed_markdown("chart:1") == "{{chart:1}}"
    assert chart_embed_markdown("1") == "{{chart:1}}"


def test_split_brace_placeholder():
    answer = "Intro\n\n{{chart:1}}\n\nOutro"
    parts = split_answer_with_charts(answer, [_spec(1)])
    assert [k for k, _ in parts] == ["md", "chart", "md"]
    assert parts[0][1].startswith("Intro")
    assert parts[1][1]["chart_id"] == "chart:1"


def test_split_markdown_link_placeholder():
    answer = "See ![Revenue](chart:2) below."
    parts = split_answer_with_charts(answer, [_spec(2)])
    assert parts == [("md", "See "), ("chart", _spec(2)), ("md", " below.")]


def test_unreferenced_charts_are_omitted():
    answer = "No embed here."
    parts = split_answer_with_charts(answer, [_spec(1), _spec(2)])
    assert parts == [("md", answer)]


def test_empty_answer_with_charts_renders_nothing():
    assert split_answer_with_charts("", [_spec(1)]) == []
    assert split_answer_with_charts("   ", [_spec(1)]) == []


def test_multiple_inline_charts():
    answer = "A {{chart:1}}\n\nB {{chart:2}}"
    parts = split_answer_with_charts(answer, [_spec(1), _spec(2)])
    assert [k for k, _ in parts] == ["md", "chart", "md", "chart"]
    assert parts[1][1]["chart_id"] == "chart:1"
    assert parts[2][1].strip() == "B"
    assert parts[3][1]["chart_id"] == "chart:2"


def test_normalize_chart_id():
    assert normalize_chart_id("1") == "chart:1"
    assert normalize_chart_id("chart:3") == "chart:3"
