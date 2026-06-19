from dbaide.rendering.answer_page import render_answer_page_html
from dbaide.rendering.compose import compose_blocks, compose_document


def _bar_chart(n: int) -> dict:
    return {
        "chart_id": f"chart:{n}",
        "chart_type": "bar",
        "title": f"T{n}",
        "categories": ["A"],
        "series": [{"name": "n", "values": [1.0]}],
        "row_count": 1,
    }


def test_compose_inline_chart_order():
    answer = "Before\n\n{{chart:1}}\n\nAfter"
    blocks = compose_blocks(answer, [_bar_chart(1)])
    assert [b["type"] for b in blocks] == ["markdown", "chart", "markdown"]
    assert blocks[0]["source"].startswith("Before")
    assert blocks[1]["chart_id"] == "chart:1"
    assert blocks[1]["spec"]["chart_type"] == "bar"
    assert isinstance(blocks[1]["echarts_option"], dict)
    assert blocks[1]["echarts_option"].get("series")
    assert blocks[2]["source"].strip() == "After"


def test_compose_appends_unreferenced_charts():
    blocks = compose_blocks("No embed.", [_bar_chart(1)])
    assert [b["type"] for b in blocks] == ["markdown", "chart"]
    assert blocks[1]["chart_id"] == "chart:1"


def test_compose_chart_only_answer():
    blocks = compose_blocks("", [_bar_chart(1)])
    assert len(blocks) == 1
    assert blocks[0]["type"] == "chart"


def test_compose_invalid_chart_becomes_warning_markdown():
    bad = {"chart_id": "chart:9", "chart_type": "bar", "title": "Bad", "categories": [], "series": []}
    blocks = compose_blocks("See {{chart:9}}", [bad])
    assert blocks[0]["type"] == "markdown"
    assert blocks[1]["type"] == "markdown"
    assert "could not be rendered" in blocks[1]["source"]


def test_compose_document_envelope():
    doc = compose_document("Hi", [], meta={"language": "zh"})
    assert doc["schema_version"] == 1
    assert doc["meta"]["language"] == "zh"
    assert doc["blocks"][0]["type"] == "markdown"


def test_answer_page_html_includes_blocks_and_scripts():
    blocks = compose_blocks("Hello **world**", [])
    html = render_answer_page_html(blocks)
    assert "Hello **world**" in html or "Hello" in html
    assert "marked" in html.lower() or "BLOCKS" in html
    assert "echarts" in html.lower() or "ECharts" in html
    assert "measureContentHeight" in html
