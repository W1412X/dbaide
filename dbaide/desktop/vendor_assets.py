"""Qt helpers for bundled WebEngine vendor scripts."""

from __future__ import annotations

from PyQt6.QtCore import QUrl

from dbaide.rendering.vendor_scripts import (
    CDN_ECHARTS,
    CDN_HLJS,
    CDN_MARKED,
    echarts_script_src,
    hljs_script_src,
    marked_script_src,
    uses_local_scripts,
    vendor_dir,
)

__all__ = [
    "CDN_ECHARTS",
    "CDN_HLJS",
    "CDN_MARKED",
    "echarts_script_src",
    "hljs_script_src",
    "marked_script_src",
    "vendor_dir",
    "vendor_base_url",
    "webengine_html_base",
]


def vendor_base_url() -> QUrl | None:
    directory = vendor_dir()
    if directory is None:
        return None
    path = str(directory.resolve())
    if not path.endswith("/"):
        path += "/"
    return QUrl.fromLocalFile(path)


def webengine_html_base(*script_srcs: str) -> QUrl:
    if uses_local_scripts(*script_srcs):
        local = vendor_base_url()
        if local is not None:
            return local
    return QUrl("about:blank")
