"""Build the full HTML page for an AI-authored interactive dashboard.

The AI writes only a declarative body: controls tagged ``data-param="name"`` and
chart containers tagged ``data-chart="chart_id"`` (plus any layout/styling). It
never writes bridge or charting code. This module wraps that body with echarts,
the QWebChannel transport, and a small injected client that:

- collects ``[data-param]`` values (checkbox groups → lists, multi-selects → lists),
- on load and on any ``[data-apply]`` click, calls the Python bridge per
  ``[data-chart]`` and renders the returned ECharts option into it.

So the data path is locked to named recipes through the bridge — the page can
never send raw SQL.
"""

from __future__ import annotations

from typing import Any

_CLIENT_JS = r"""
(function(){
  var bridge=null, ready=false, q=[], insts={};
  function whenReady(fn){ ready?fn():q.push(fn); }
  function init(){
    if(typeof QWebChannel==='undefined' || !window.qt){ return; }
    new QWebChannel(qt.webChannelTransport, function(ch){
      bridge=ch.objects.bridge; ready=true; q.forEach(function(f){f();}); q=[]; refresh();
    });
  }
  function query(cid, params){
    return new Promise(function(resolve){
      bridge.query(cid, JSON.stringify(params||{}), function(payload){
        var r=null; try{ r=JSON.parse(payload); }catch(e){} resolve(r);
      });
    });
  }
  function collectParams(){
    var out={};
    document.querySelectorAll('[data-param]').forEach(function(el){
      var n=el.getAttribute('data-param');
      if(el.type==='checkbox'){ if(!(n in out)) out[n]=[]; if(el.checked) out[n].push(el.value); }
      else if(el.tagName==='SELECT' && el.multiple){ out[n]=Array.prototype.map.call(el.selectedOptions,function(o){return o.value;}); }
      else { out[n]=el.value; }
    });
    return out;
  }
  function renderChart(cid, params){
    var el=document.querySelector('[data-chart="'+cid+'"]');
    if(!el || !window.echarts) return;
    query(cid, params).then(function(res){
      if(!res || res.error || !res.echarts_option){
        el.classList.add('dbaide-empty'); el.textContent=(res&&res.error)?res.error:'无数据'; return;
      }
      el.classList.remove('dbaide-empty');
      var inst=insts[cid]; if(!inst){ inst=echarts.init(el); insts[cid]=inst; }
      inst.setOption(res.echarts_option, true); inst.resize();
    });
  }
  function refresh(){
    whenReady(function(){
      var params=collectParams();
      document.querySelectorAll('[data-chart]').forEach(function(el){ renderChart(el.getAttribute('data-chart'), params); });
    });
  }
  window.dbaide={query:query, collectParams:collectParams, renderChart:renderChart, refresh:refresh};
  window.addEventListener('resize', function(){ Object.keys(insts).forEach(function(k){ insts[k].resize(); }); });
  function wire(){
    document.querySelectorAll('[data-apply]').forEach(function(b){
      b.addEventListener('click', function(e){ e.preventDefault(); refresh(); });
    });
    init();
  }
  if(document.readyState!=='loading') wire(); else document.addEventListener('DOMContentLoaded', wire);
})();
"""


def _base_css(theme: dict[str, Any]) -> str:
    t = theme or {}
    # every colour comes from the injected app theme (no hardcoded palette) so the page
    # matches the app and adapts when the theme changes; fallbacks only guard a missing key
    return f"""
    :root {{
      --text: {t.get('text', '#f4f4f5')}; --text2: {t.get('text2', '#b7bec9')};
      --muted: {t.get('muted', '#737b89')}; --bg: {t.get('bg', '#07080a')};
      --panel: {t.get('panel', '#111419')}; --panel2: {t.get('panel2', '#151922')};
      --border: {t.get('border', '#1b2026')}; --accent: {t.get('accent', '#3b82f6')};
      --accent-text: {t.get('accent_text', '#ffffff')};
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text);
      font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:13px; }}
    #dbaide-root {{ padding:16px; }}
    [data-chart] {{ width:100%; min-height:260px; background:var(--panel); border:1px solid var(--border);
      border-radius:8px; }}
    [data-chart].dbaide-empty {{ display:flex; align-items:center; justify-content:center;
      color:var(--muted); min-height:200px; }}
    input, select {{ font:inherit; color:var(--text); background:var(--panel2);
      border:1px solid var(--border); border-radius:6px; padding:6px 9px; min-width:120px; }}
    input:focus, select:focus {{ outline:none; border-color:var(--accent); }}
    button {{ font:inherit; color:var(--accent-text); background:var(--accent); border:none;
      border-radius:6px; padding:7px 16px; cursor:pointer; font-weight:600; }}
    button:hover {{ filter:brightness(1.08); }}
    label {{ color:var(--text2); font-size:11px; }}
    /* the layout classes used by the generated body (and recommended to the model) */
    .dbaide-controls {{ display:flex; flex-wrap:wrap; gap:14px; align-items:flex-end;
      margin-bottom:16px; padding:14px 16px; background:var(--panel); border:1px solid var(--border);
      border-radius:10px; }}
    .dbaide-controls label {{ display:flex; flex-direction:column; gap:5px; }}
    .dbaide-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:14px; }}
    .dbaide-card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px;
      padding:14px 16px; }}
    .dbaide-card-title {{ color:var(--text); font-weight:600; font-size:13px; margin-bottom:10px; }}
    .dbaide-card [data-chart] {{ background:transparent; border:none; min-height:0; }}
    """


def build_dashboard_page(
    body_html: str,
    *,
    echarts_src: str,
    theme: dict[str, Any] | None = None,
    qwebchannel: bool = True,
) -> str:
    """Wrap the AI-authored *body_html* into a full, bridge-wired page."""
    channel = '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>' if qwebchannel else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<style>{_base_css(theme or {})}</style>"
        f"{channel}"
        f"<script src=\"{echarts_src}\"></script>"
        "</head><body>"
        f"<div id=\"dbaide-root\">{body_html or ''}</div>"
        f"<script>{_CLIENT_JS}</script>"
        "</body></html>"
    )
