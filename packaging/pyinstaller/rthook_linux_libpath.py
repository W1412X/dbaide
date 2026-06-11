"""Prepend bundled ``lib/`` to ``LD_LIBRARY_PATH`` before Qt loads the xcb plugin."""

from __future__ import annotations

import os
import sys

if sys.platform == "linux":
    base = getattr(sys, "_MEIPASS", None)
    if base:
        lib = os.path.join(base, "lib")
        if os.path.isdir(lib):
            prev = os.environ.get("LD_LIBRARY_PATH", "")
            os.environ["LD_LIBRARY_PATH"] = lib + (os.pathsep + prev if prev else "")
