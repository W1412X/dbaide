#!/usr/bin/env python3
"""Preview a composed answer document in the default browser."""

from __future__ import annotations

import argparse
import json
import tempfile
import webbrowser
from pathlib import Path

from dbaide.rendering.answer_page import render_answer_page_html
from dbaide.rendering.compose import compose_document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answer", default="", help="Answer markdown text")
    parser.add_argument("--charts", default="", help="Path to charts JSON array")
    args = parser.parse_args()

    charts: list = []
    if args.charts:
        charts = json.loads(Path(args.charts).read_text(encoding="utf-8"))

    doc = compose_document(args.answer, charts)
    html = render_answer_page_html(doc["blocks"])
    path = Path(tempfile.gettempdir()) / "dbaide_answer_preview.html"
    path.write_text(html, encoding="utf-8")
    webbrowser.open(path.as_uri())
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
