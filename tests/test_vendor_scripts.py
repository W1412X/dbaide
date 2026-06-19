from pathlib import Path

from dbaide.rendering.vendor_scripts import (
    CDN_ECHARTS,
    CDN_HLJS,
    CDN_MARKED,
    echarts_script_src,
    hljs_script_src,
    marked_script_src,
    vendor_dir,
)


def test_vendor_dir_contains_bundled_scripts():
    directory = vendor_dir()
    assert directory is not None
    assert (directory / "marked.umd.js").is_file()
    assert (directory / "highlight.min.js").is_file()
    assert (directory / "echarts.min.js").is_file()


def test_script_src_prefers_local_relative_names():
    assert marked_script_src() == "marked.umd.js"
    assert hljs_script_src() == "highlight.min.js"
    assert echarts_script_src() == "echarts.min.js"


def test_script_src_env_override(monkeypatch):
    monkeypatch.setenv("DBAIDE_MARKED_SRC", "https://example.test/marked.js")
    assert marked_script_src() == "https://example.test/marked.js"


def test_cdn_constants_are_https():
    for url in (CDN_MARKED, CDN_HLJS, CDN_ECHARTS):
        assert url.startswith("https://")
