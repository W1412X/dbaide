"""Self-contained HTML pages for Markdown rendering inside Qt WebEngine.

GUI-free: maps Markdown source + theme tokens to a full HTML document using
standard browser libraries (marked + highlight.js). The desktop layer hosts the
page and handles height sync — streaming answers stay on QPlainTextEdit until
``complete_turn`` swaps in a single WebEngine render (no per-chunk HTML).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from dbaide.rendering.answer_page import script_json
from dbaide.rendering.vendor_scripts import hljs_script_src, marked_script_src


def render_markdown_html(
    markdown: str,
    *,
    theme: Mapping[str, Any] | None = None,
    marked_src: str | None = None,
    hljs_src: str | None = None,
) -> str:
    """Build a themed HTML document that renders *markdown* in the browser."""
    theme = dict(theme or {})
    marked = str(marked_src if marked_src is not None else marked_script_src())
    hljs = str(hljs_src if hljs_src is not None else hljs_script_src())
    text = str(theme.get("text") or "#eef1f5")
    text2 = str(theme.get("text2") or text)
    muted = str(theme.get("muted") or "#737b89")
    border = str(theme.get("border") or "#1b2026")
    code_bg = str(theme.get("codeBg") or "#090b0f")
    panel2 = str(theme.get("panel2") or "#151922")
    link = str(theme.get("link") or "#67a7ff")
    bg = str(theme.get("bg") or "transparent")
    # markdown is untrusted (model output / DB values) → must be <script>-safe.
    md_json = script_json(str(markdown or ""))
    marked_json = json.dumps(marked, ensure_ascii=False)
    hljs_json = json.dumps(hljs, ensure_ascii=False)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body {{
      margin: 0; padding: 0; width: 100%; height: auto; min-height: 0;
      background: {bg};
      color: {text};
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 13px; line-height: 1.55;
      overflow: hidden;
    }}
    #root {{ padding: 0 2px 0 0; }}
    #root > :first-child {{ margin-top: 0; }}
    #root > :last-child {{ margin-bottom: 0; }}
    h1 {{ font-size: 18px; font-weight: 700; margin: 12px 0 6px; }}
    h2 {{ font-size: 16px; font-weight: 700; margin: 10px 0 6px; }}
    h3 {{ font-size: 14px; font-weight: 600; margin: 9px 0 4px; }}
    h4, h5, h6 {{ font-size: 13px; font-weight: 600; margin: 8px 0 4px; color: {text2}; }}
    p {{ margin: 5px 0; }}
    ul, ol {{ margin: 4px 0; padding-left: 1.35em; }}
    li {{ margin: 2px 0; }}
    blockquote {{
      margin: 6px 0; padding: 6px 12px;
      background: {panel2}; color: {text2};
      border-left: 3px solid {border};
    }}
    hr {{ border: none; height: 1px; background: {border}; margin: 10px 0; }}
    a {{ color: {link}; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{
      background: {code_bg}; padding: 1px 5px; border-radius: 4px;
      font-family: Menlo, Monaco, Consolas, monospace; font-size: 12px;
    }}
    pre {{
      background: {code_bg}; border: 1px solid {border}; border-radius: 8px;
      padding: 10px 12px; overflow-x: auto; margin: 8px 0;
    }}
    pre code {{ background: transparent; padding: 0; border-radius: 0; }}
    table {{ border-collapse: collapse; margin: 8px 0; width: 100%; }}
    th, td {{
      border-bottom: 1px solid {border};
      padding: 7px 14px 7px 0; text-align: left; vertical-align: top;
    }}
    th {{ color: {muted}; font-weight: 600; }}
    .hljs {{ background: {code_bg}; color: {text}; }}
  </style>
  <script src={marked_json}></script>
  <script src={hljs_json}></script>
</head>
<body>
  <div id="root"></div>
  <script>
    const md = {md_json};
    function render() {{
      const root = document.getElementById('root');
      if (!window.marked) {{
        root.textContent = md;
        return;
      }}
      if (window.hljs && window.marked && typeof marked.use === 'function') {{
        marked.use({{
          breaks: true,
          gfm: true,
        }});
        marked.setOptions({{
          highlight: (code, lang) => {{
            if (window.hljs) {{
              if (lang && hljs.getLanguage(lang)) {{
                return hljs.highlight(code, {{ language: lang }}).value;
              }}
              return hljs.highlightAuto(code).value;
            }}
            return code;
          }},
        }});
      }}
      root.innerHTML = marked.parse(md);
      if (window.hljs && typeof hljs.highlightAll === 'function') {{
        hljs.highlightAll();
      }}
    }}
    function measureContentHeight() {{
      const root = document.getElementById('root');
      if (!root) return 0;
      const rect = root.getBoundingClientRect();
      const style = window.getComputedStyle(root);
      const mt = parseFloat(style.marginTop) || 0;
      const mb = parseFloat(style.marginBottom) || 0;
      return Math.ceil(rect.height + mt + mb);
    }}
    render();
  </script>
</body>
</html>"""
