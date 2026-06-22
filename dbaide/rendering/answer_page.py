"""Self-contained HTML for unified assistant answers (Markdown + charts).

GUI-free: consumes an ephemeral AnswerDocument block list produced by
``dbaide.rendering.compose`` and maps it to a single WebEngine page.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from dbaide.rendering.vendor_scripts import echarts_script_src, hljs_script_src, marked_script_src


def render_answer_page_html(
    blocks: list[dict[str, Any]],
    *,
    theme: Mapping[str, Any] | None = None,
    marked_src: str | None = None,
    hljs_src: str | None = None,
    echarts_src: str | None = None,
    document_title: str = "",
    for_export: bool = False,
    root_padding: str | None = None,
) -> str:
    """Build a themed HTML document that renders *blocks* in order."""
    theme = dict(theme or {})
    marked = str(marked_src if marked_src is not None else marked_script_src())
    hljs = str(hljs_src if hljs_src is not None else hljs_script_src())
    echarts = str(echarts_src if echarts_src is not None else echarts_script_src())
    text = str(theme.get("text") or "#eef1f5")
    text2 = str(theme.get("text2") or text)
    muted = str(theme.get("muted") or "#737b89")
    border = str(theme.get("border") or "#1b2026")
    code_bg = str(theme.get("codeBg") or "#090b0f")
    panel = str(theme.get("panel") or theme.get("bg") or "#07080a")
    panel2 = str(theme.get("panel2") or "#151922")
    link = str(theme.get("link") or "#67a7ff")
    bg = str(theme.get("bg") or "#07080a") if for_export else "transparent"
    body_overflow = "auto" if for_export else "hidden"
    root_pad = str(root_padding if root_padding is not None else "0 2px 0 0")
    title_text = str(document_title or "").strip()
    title_tag = f"  <title>{_html_escape(title_text)}</title>\n" if title_text else ""
    # blocks carry untrusted markdown + DB-derived chart data → must be <script>-safe.
    blocks_json = script_json(list(blocks or []))
    marked_json = json.dumps(marked, ensure_ascii=False)
    hljs_json = json.dumps(hljs, ensure_ascii=False)
    echarts_json = json.dumps(echarts, ensure_ascii=False)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
{title_tag}  <style>
    html, body {{
      margin: 0; padding: 0; width: 100%; height: auto; min-height: 0;
      background: {bg};
      color: {text};
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 13px; line-height: 1.55;
      overflow: {body_overflow};
    }}
    ::selection {{ background: rgba(59, 130, 246, 0.32); }}
    #root {{ padding: {root_pad}; }}
    #root > :first-child {{ margin-top: 0; }}
    #root > :last-child {{ margin-bottom: 0; }}
    .md-block > :first-child {{ margin-top: 0; }}
    .md-block > :last-child {{ margin-bottom: 0; }}
    .md-block h1 {{ font-size: 18px; font-weight: 700; margin: 12px 0 6px; }}
    .md-block h2 {{ font-size: 16px; font-weight: 700; margin: 10px 0 6px; }}
    .md-block h3 {{ font-size: 14px; font-weight: 600; margin: 9px 0 4px; }}
    .md-block h4, .md-block h5, .md-block h6 {{
      font-size: 13px; font-weight: 600; margin: 8px 0 4px; color: {text2};
    }}
    .md-block p {{ margin: 5px 0; }}
    .md-block ul, .md-block ol {{ margin: 4px 0; padding-left: 1.35em; }}
    .md-block li {{ margin: 2px 0; }}
    .md-block blockquote {{
      margin: 6px 0; padding: 6px 12px;
      background: {panel2}; color: {text2};
      border-left: 3px solid {border};
    }}
    .md-block hr {{ border: none; height: 1px; background: {border}; margin: 10px 0; }}
    .md-block a {{ color: {link}; text-decoration: none; }}
    .md-block a:hover {{ text-decoration: underline; }}
    .md-block code {{
      background: {code_bg}; padding: 1px 5px; border-radius: 4px;
      font-family: Menlo, Monaco, Consolas, monospace; font-size: 12px;
    }}
    .md-block pre {{
      background: {code_bg}; border: 1px solid {border}; border-radius: 8px;
      padding: 10px 12px; overflow-x: auto; margin: 8px 0;
    }}
    .md-block pre code {{ background: transparent; padding: 0; border-radius: 0; }}
    .md-block table {{ border-collapse: collapse; margin: 8px 0; width: 100%; }}
    .md-block th, .md-block td {{
      border-bottom: 1px solid {border};
      padding: 7px 14px 7px 0; text-align: left; vertical-align: top;
    }}
    .md-block th {{ color: {muted}; font-weight: 600; }}
    .md-block .hljs {{ background: {code_bg}; color: {text}; }}
    .chart-block {{
      margin: 12px 0;
      padding: 0;
    }}
    .chart-title {{
      margin: 0 0 4px;
      font-size: 13px;
      font-weight: 600;
      color: {text};
    }}
    .chart-canvas {{
      width: 100%;
      min-height: 240px;
    }}
    .chart-error {{
      color: #ef4444;
      font-size: 13px;
      padding: 8px 0;
    }}
  </style>
  <script src={marked_json}></script>
  <script src={hljs_json}></script>
  <script src={echarts_json}></script>
</head>
<body>
  <div id="root"></div>
  <script>
    const BLOCKS = {blocks_json};
    const chartInstances = [];

    function configureMarked() {{
      if (!window.marked) return;
      if (typeof marked.use === 'function') {{
        marked.use({{ breaks: true, gfm: true }});
      }}
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

    function renderMarkdown(source) {{
      const section = document.createElement('section');
      section.className = 'md-block';
      if (!window.marked) {{
        section.textContent = source;
        return section;
      }}
      section.innerHTML = marked.parse(source);
      if (window.hljs && typeof hljs.highlightAll === 'function') {{
        section.querySelectorAll('pre code').forEach((el) => {{
          try {{ hljs.highlightElement(el); }} catch (e) {{}}
        }});
      }}
      return section;
    }}

    function compactAxisValue(value) {{
      const n = Number(value);
      if (!Number.isFinite(n)) return value;
      const abs = Math.abs(n);
      if (abs >= 1e8) return (n / 1e8).toFixed(1).replace(/\\.0$/, '') + '\\u4ebf';
      if (abs >= 1e4) return (n / 1e4).toFixed(1).replace(/\\.0$/, '') + '\\u4e07';
      if (abs >= 1000) return (n / 1000).toFixed(1).replace(/\\.0$/, '') + 'k';
      if (Math.abs(n - Math.round(n)) < 1e-6) return String(Math.round(n));
      return n.toFixed(2).replace(/0+$/, '').replace(/\\.$/, '');
    }}

    function applyRuntimeFormatters(root) {{
      const patch = (axis) => {{
        if (!axis || axis.type !== 'value') return;
        axis.axisLabel = axis.axisLabel || {{}};
        if (axis._compactValues) {{
          if (axis._valueFormat === 'currency') {{
            axis.axisLabel.formatter = (v) => '$' + compactAxisValue(v);
          }} else if (axis._valueFormat !== 'percent') {{
            axis.axisLabel.formatter = compactAxisValue;
          }}
          delete axis._compactValues;
          delete axis._valueFormat;
        }}
        if (axis.name) {{
          axis.nameGap = Math.max(axis.nameGap || 0, 14);
          axis.nameLocation = axis.nameLocation || 'end';
          axis.nameTruncate = {{ maxWidth: 96, ellipsis: '…' }};
        }}
      }};
      const axes = Array.isArray(root.yAxis) ? root.yAxis : [root.yAxis];
      axes.forEach(patch);
      const xAxes = Array.isArray(root.xAxis) ? root.xAxis : [root.xAxis];
      xAxes.forEach((axis) => {{
        if (axis && axis.type === 'value') patch(axis);
      }});
    }}

    function chartDomId(chartId) {{
      return 'chart-' + String(chartId || 'x').replace(/[^a-zA-Z0-9_-]+/g, '-');
    }}

    function renderChart(block) {{
      const wrap = document.createElement('section');
      wrap.className = 'chart-block';
      const title = String(block.title || '').trim();
      if (title) {{
        const h = document.createElement('h3');
        h.className = 'chart-title';
        h.textContent = title;
        wrap.appendChild(h);
      }}
      const canvas = document.createElement('div');
      canvas.className = 'chart-canvas';
      canvas.id = chartDomId(block.chart_id);
      canvas.style.height = Math.max(240, Number(block.height) || 320) + 'px';
      wrap.appendChild(canvas);
      if (!window.echarts) {{
        const err = document.createElement('div');
        err.className = 'chart-error';
        err.textContent = 'ECharts failed to load.';
        wrap.appendChild(err);
        return wrap;
      }}
      const option = block.echarts_option || {{}};
      applyRuntimeFormatters(option);
      const chart = echarts.init(canvas, null, {{ renderer: 'canvas' }});
      chart.setOption(option, true);
      chartInstances.push(chart);
      const resize = () => {{ try {{ chart.resize(); }} catch (e) {{}} }};
      window.addEventListener('resize', resize);
      if (window.ResizeObserver) {{
        new ResizeObserver(resize).observe(canvas);
      }}
      setTimeout(resize, 0);
      requestAnimationFrame(resize);
      return wrap;
    }}

    function renderDocument() {{
      const root = document.getElementById('root');
      configureMarked();
      for (const block of BLOCKS) {{
        if (!block || typeof block !== 'object') continue;
        if (block.type === 'markdown') {{
          root.appendChild(renderMarkdown(String(block.source || '')));
        }} else if (block.type === 'chart') {{
          root.appendChild(renderChart(block));
        }}
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

    renderDocument();
  </script>
</body>
</html>"""


def _html_escape(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def script_json(obj: Any) -> str:
    """json.dumps for embedding inside an inline <script>. json.dumps does NOT escape
    '/', so a value containing '</script>' (from untrusted DB content or model output)
    would close the script tag and let injected HTML/JS execute. Escape '<','>','&' and
    the JS line separators U+2028/U+2029 to \\uXXXX — still valid JSON, no break-out."""
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )
