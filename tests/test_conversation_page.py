"""The single-document conversation page renders the whole transcript in one
WebEngine view (so memory stays bounded — QtWebEngine never reclaims per-view
renderer processes). These cover the pure HTML/JS builder; the live render + memory
bound is exercised by a WebEngine smoke run."""

from __future__ import annotations

from dbaide.rendering.conversation_page import build_conversation_page


def _build(**kw):
    kw.setdefault("marked_src", "marked.js")
    kw.setdefault("hljs_src", "hljs.js")
    kw.setdefault("echarts_src", "echarts.js")
    return build_conversation_page(**kw)


def test_page_has_shell_and_controller():
    html = _build()
    assert html.lstrip().startswith("<!doctype html>")
    assert '<div id="dbc-root">' in html
    assert "window.DBChat" in html
    # the controller API the Python side drives
    for fn in ("render:", "setTurn:", "appendStream:", "setStatus:", "setAgenda:", "setTheme:", "clearAll:"):
        assert fn in html, fn
    # vendor scripts wired
    assert '"marked.js"' in html and '"hljs.js"' in html and '"echarts.js"' in html


def test_theme_injected_as_css_vars():
    html = _build(theme={"text": "#abcdef", "bg": "#101010", "accent": "#ff0044", "panel": "#222"})
    assert "--text:#abcdef" in html
    assert "--accent:#ff0044" in html
    assert "--bg:#101010" in html
    # markdown + chart styling present (reused from the answer page)
    assert ".md-block" in html and ".chart-canvas" in html


def test_theme_defaults_when_missing():
    html = _build(theme={})
    # a sane default palette so the page never renders unstyled
    assert "--text:#eef1f5" in html and "--bg:#07080a" in html


def test_initial_turns_are_script_safe():
    # untrusted user text / DB content must not break out of the inline <script>
    evil = 'hi </script><img src=x onerror=alert(1)>'
    html = _build(initial_turns=[{"id": "t1", "user": {"text": evil}}])
    assert "window.__DBC_INITIAL__" in html
    assert "</script><img" not in html  # escaped, no tag breakout
    assert "\\u003c" in html  # script_json escaped the angle brackets


def test_empty_page_is_valid():
    html = _build(initial_turns=[])
    assert "__DBC_INITIAL__ = []" in html
    assert html.count("<body>") == 1 and html.count("</body>") == 1


def test_charts_are_lazily_virtualized():
    # ECharts instances are created only near the viewport and disposed when far, so
    # memory stays bounded regardless of how many charts the conversation accumulates.
    html = _build()
    assert "IntersectionObserver" in html
    assert "initChart" in html and "releaseChart" in html
    assert "disposeCharts" in html


def test_qwebchannel_script_included_by_default():
    # the bridge (clarification submit / action buttons / trace toggle) needs it
    assert "qrc:///qtwebchannel/qwebchannel.js" in _build()
    assert "qrc:///qtwebchannel/qwebchannel.js" not in _build(qwebchannel=False)
