from dbaide.rendering.answer_export import export_answer_html, suggest_export_filename


def _bar_chart(n: int) -> dict:
    return {
        "chart_id": f"chart:{n}",
        "chart_type": "bar",
        "title": f"Sales {n}",
        "categories": ["A", "B"],
        "series": [{"name": "n", "values": [1.0, 2.0]}],
        "row_count": 2,
    }


def test_suggest_export_filename():
    assert suggest_export_filename("") == "dbaide-answer.html"
    assert suggest_export_filename("How many orders?").endswith(".html")
    assert "How-many-orders" in suggest_export_filename("How many orders?")


def test_export_answer_html_includes_markdown_and_chart():
    answer = "Hello **world**\n\n{{chart:1}}\n\nDone."
    html = export_answer_html(answer, [_bar_chart(1)], title="My question")
    assert "<!doctype html>" in html.lower()
    assert "<title>My question</title>" in html
    assert "Hello **world**" in html or "marked" in html.lower()
    assert "echarts" in html.lower()
    assert "overflow: auto" in html


def test_export_answer_html_uses_cdn_scripts():
    html = export_answer_html("Plain text", [])
    assert "https://unpkg.com/marked" in html
    assert "https://unpkg.com/echarts" in html


def test_export_answer_html_custom_padding():
    html = export_answer_html(
        "Hello",
        [],
        root_padding="12px 24px 36px 8px",
    )
    assert "padding: 12px 24px 36px 8px" in html


def test_format_root_padding():
    from dbaide.rendering.answer_render import format_root_padding

    assert format_root_padding(16, 20, 32, 20) == "16px 20px 32px 20px"
    assert format_root_padding(-1, 0, 0, 0) == "0px 0px 0px 0px"


def test_export_matches_shared_render_path():
    from dbaide.rendering.answer_render import build_answer_document_html, default_answer_theme
    from dbaide.rendering.vendor_scripts import CDN_ECHARTS, CDN_HLJS, CDN_MARKED

    answer = "Hello **world**"
    charts = [_bar_chart(1)]
    theme = default_answer_theme()
    export_html = export_answer_html(answer, charts, title="Q", theme=theme)
    shared_html, blocks = build_answer_document_html(
        answer,
        charts,
        theme=theme,
        marked_src=CDN_MARKED,
        hljs_src=CDN_HLJS,
        echarts_src=CDN_ECHARTS,
        document_title="Q",
        standalone=True,
        root_padding="16px 20px 32px 20px",
    )
    assert export_html == shared_html
    assert any(b.get("type") == "chart" for b in blocks)
