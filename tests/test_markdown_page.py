from dbaide.rendering.markdown_page import render_markdown_html


def test_render_markdown_html_embeds_source_and_libraries():
    html = render_markdown_html("# Title\n\n```sql\nSELECT 1\n```")
    assert "marked.umd.js" in html or "marked" in html
    assert "highlight.min.js" in html or "highlight" in html
    assert "# Title" in html
    assert "SELECT 1" in html
    assert "hljs" in html
    assert "measureContentHeight" in html


def test_render_markdown_html_applies_theme_tokens():
    html = render_markdown_html("hello", theme={"text": "#abcdef", "link": "#112233", "bg": "#010203"})
    assert "#abcdef" in html
    assert "#112233" in html
    assert "#010203" in html
