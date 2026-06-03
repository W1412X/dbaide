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


def test_render_markdown_pipe_table():
    md = (
        "根据查询，当前连接中可用的数据库如下：\n\n"
        "| 序号 | 数据库名称 |\n"
        "|------|------------|\n"
        "| 1 | perf |\n"
        "| 2 | order_data |\n"
        "| 3 | bench |\n"
    )
    html = render_markdown_safe(md)
    assert "<table" in html
    assert "<th" in html
    assert "<td" in html
    assert "perf" in html
    assert "order_data" in html
    assert "| 序号 |" not in html
    assert "<p>| 1 |" not in html


def test_loose_asterisks_not_italicized():
    """Regex renderers turn `2 * 3 = 6 and 4 * 5` into spurious italics; a real
    parser leaves arithmetic asterisks alone."""
    html = render_markdown_safe("Revenue = 2 * 3 = 6 and margin 4 * 5 percent.")
    assert "<em>" not in html
    assert "2 * 3 = 6" in html


def test_blockquote_renders():
    html = render_markdown_safe("> A note about the result.\n> second line.")
    assert "<blockquote>" in html
    assert "&gt; A note" not in html  # not shown as a literal '>'


def test_inline_code_with_stars_not_mangled():
    html = render_markdown_safe("Run `SELECT a*b FROM t` then **go**.")
    assert "<code>SELECT a*b FROM t</code>" in html
    assert "<strong>go</strong>" in html


def test_raw_html_is_escaped():
    html = render_markdown_safe("Hello <script>alert(1)</script> world")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
