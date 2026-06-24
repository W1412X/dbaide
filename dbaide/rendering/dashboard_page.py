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
    return f"""
    :root {{
      --text: {t.get('text', '#f4f4f5')}; --muted: {t.get('muted', '#a1a1aa')};
      --bg: {t.get('bg', '#161618')}; --panel: {t.get('panel', '#1f1f23')};
      --border: {t.get('border', '#33333a')}; --accent: {t.get('accent', '#6366f1')};
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text);
      font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:13px; }}
    #dbaide-root {{ padding:16px; }}
    [data-chart] {{ width:100%; min-height:260px; }}
    [data-chart].dbaide-empty {{ display:flex; align-items:center; justify-content:center;
      color:var(--muted); min-height:200px; }}
    input, select, button {{ font:inherit; color:var(--text); background:var(--panel);
      border:1px solid var(--border); border-radius:6px; padding:5px 8px; }}
    button {{ cursor:pointer; }}
    label {{ color:var(--muted); font-size:11px; }}
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
