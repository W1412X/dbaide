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


def test_render_markdown_html_escapes_script_breakout():
    """Untrusted markdown containing </script> must not break out of the inline
    <script> that holds the source (would allow arbitrary JS in the WebEngine page)."""
    evil = "hi </script><script>window.__pwned=1</script>"
    html = render_markdown_html(evil)
    assert "</script><script>window.__pwned" not in html   # raw break-out gone
    assert "\\u003c/script\\u003e" in html                 # escaped form present


def test_answer_page_escapes_script_breakout():
    from dbaide.rendering.answer_page import render_answer_page_html, script_json
    evil = "x </script><script>bad()</script>"
    html = render_answer_page_html(blocks=[{"kind": "markdown", "source": evil}])
    assert "</script><script>bad()" not in html
    # script_json also neutralizes the JS line separators U+2028/U+2029
    assert " " not in script_json("a b") and "\\u2028" in script_json("a b")
