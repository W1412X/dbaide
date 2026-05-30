from dbaide.rendering.markdown import render_markdown_safe


def test_render_markdown_preserves_multiline_json_codeblock():
    md = (
        "**Assets built**\n\n"
        "```json\n"
        "{\n"
        '  "elapsed_seconds": 845.1034550666809,\n'
        '  "tables": 3\n'
        "}\n"
        "```"
    )
    html = render_markdown_safe(md)
    assert "elapsed_seconds" in html
    assert "<pre" in html
    assert "</pre>" in html
    assert "<p><pre" not in html.replace(" ", "")
    assert html.count("<pre") == 1


def test_render_markdown_preserves_sql_codeblock():
    md = "```sql\nSELECT *\nFROM orders\nWHERE id = 1\n```"
    html = render_markdown_safe(md)
    assert "FROM orders" in html
    assert "<pre" in html
    assert "<p>SELECT" not in html
