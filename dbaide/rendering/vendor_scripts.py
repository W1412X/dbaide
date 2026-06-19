"""Bundled front-end script paths for WebEngine HTML (no Qt dependency)."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

CDN_MARKED = "https://unpkg.com/marked@12.0.2/lib/marked.umd.js"
CDN_HLJS = "https://unpkg.com/@highlightjs/cdn-assets@11.9.0/highlight.min.js"
CDN_ECHARTS = "https://unpkg.com/echarts@5.6.0/dist/echarts.min.js"

_VENDOR_MARKED = "marked.umd.js"
_VENDOR_HLJS = "highlight.min.js"
_VENDOR_ECHARTS = "echarts.min.js"

_ENV_MARKED = "DBAIDE_MARKED_SRC"
_ENV_HLJS = "DBAIDE_HLJS_SRC"
_ENV_ECHARTS = "DBAIDE_ECHARTS_SRC"


@lru_cache(maxsize=1)
def vendor_dir() -> Path | None:
    """Directory containing bundled ``*.js`` vendor files, if available."""
    candidates: list[Path] = []
    pkg_vendor = Path(__file__).resolve().parents[1] / "desktop" / "assets" / "vendor"
    candidates.append(pkg_vendor)
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", "") or "")
        if meipass:
            candidates.extend([
                meipass / "dbaide" / "desktop" / "assets" / "vendor",
                meipass / "desktop" / "assets" / "vendor",
            ])
    for directory in candidates:
        if (directory / _VENDOR_MARKED).is_file():
            return directory
    return None


def _env_override(env_key: str) -> str:
    return str(os.environ.get(env_key) or "").strip()


def script_src(filename: str, *, env_key: str, cdn_url: str) -> str:
    """Resolve a ``<script src=…>`` value (relative local name or absolute URL)."""
    override = _env_override(env_key)
    if override:
        return override
    directory = vendor_dir()
    if directory is not None and (directory / filename).is_file():
        return filename
    return cdn_url


def marked_script_src() -> str:
    return script_src(_VENDOR_MARKED, env_key=_ENV_MARKED, cdn_url=CDN_MARKED)


def hljs_script_src() -> str:
    return script_src(_VENDOR_HLJS, env_key=_ENV_HLJS, cdn_url=CDN_HLJS)


def echarts_script_src() -> str:
    return script_src(_VENDOR_ECHARTS, env_key=_ENV_ECHARTS, cdn_url=CDN_ECHARTS)


def uses_local_scripts(*script_srcs: str) -> bool:
    sources = [str(s or "").strip() for s in script_srcs if str(s or "").strip()]
    return bool(sources) and all(not s.startswith(("http://", "https://")) for s in sources)
