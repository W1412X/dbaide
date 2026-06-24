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
  var bridge=null, ready=false, q=[], cache=null;
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
  function esc(v){ return (v==null?'':String(v)).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }
  function fmtNum(v){ return (typeof v==='number')?v.toLocaleString():esc(v); }
  function kpiValue(res){
    var rows=res.rows||[], cols=res.columns||[];
    if(!rows.length || !cols.length) return null;
    var r=rows[0], val=null;
    for(var i=0;i<cols.length;i++){ if(typeof r[cols[i]]==='number') val=r[cols[i]]; }  // last numeric
    return (val===null)?r[cols[cols.length-1]]:val;
  }
  function renderTable(el, res){
    var cols=res.columns||[], rows=res.rows||[];
    var head='<tr>'+cols.map(function(c){return '<th>'+esc(c)+'</th>';}).join('')+'</tr>';
    var body=rows.slice(0,100).map(function(r){
      return '<tr>'+cols.map(function(c){return '<td>'+esc(r[c])+'</td>';}).join('')+'</tr>'; }).join('');
    el.innerHTML='<table class="dbaide-table"><thead>'+head+'</thead><tbody>'+body+'</tbody></table>';
  }
  function cachedQuery(cid, params){
    // dedup within a refresh: one recipe feeding kpi+chart+table runs its SQL once
    if(!cache) return query(cid, params);
    var k=cid+'|'+JSON.stringify(params||{});
    return cache[k] || (cache[k]=query(cid, params));
  }
  function renderTile(el, params){
    var cid=el.getAttribute('data-chart'), kind=el.getAttribute('data-kind')||'chart';
    cachedQuery(cid, params).then(function(res){
      if(!res || res.error){ el.classList.add('dbaide-empty'); el.textContent=(res&&res.error)?res.error:'无数据'; return; }
      el.classList.remove('dbaide-empty');
      if(kind==='kpi'){ var v=kpiValue(res); el.textContent=(v==null)?'—':fmtNum(v); return; }
      if(kind==='table'){
        if(!res.rows || !res.rows.length){ el.classList.add('dbaide-empty'); el.textContent='无数据'; }
        else renderTable(el, res);
        return;
      }
      if(!res.echarts_option){ el.classList.add('dbaide-empty'); el.textContent='无数据'; return; }
      if(!window.echarts) return;
      // key the instance by the ELEMENT (getInstanceByDom), not the chart_id — the same
      // recipe can drive several chart tiles, which would collide on a chart_id map
      var inst=echarts.getInstanceByDom(el) || echarts.init(el);
      inst.setOption(res.echarts_option, true); inst.resize();
    });
  }
  function refresh(){
    whenReady(function(){
      var params=collectParams();
      cache={};   // fresh dedup cache per refresh (params are constant within one)
      document.querySelectorAll('[data-chart]').forEach(function(el){ renderTile(el, params); });
    });
  }
  window.dbaide={query:query, collectParams:collectParams, renderTile:renderTile, refresh:refresh};
  window.addEventListener('resize', function(){
    if(!window.echarts) return;
    document.querySelectorAll('[data-kind="chart"]').forEach(function(el){
      var i=echarts.getInstanceByDom(el); if(i) i.resize(); });
  });
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
    .dbaide-controls {{ display:flex; flex-wrap:wrap; gap:16px; align-items:flex-end;
      margin-bottom:16px; padding:14px 16px; background:var(--panel); border:1px solid var(--border);
      border-radius:10px; }}
    .dbaide-controls label, .dbaide-field {{ display:flex; flex-direction:column; gap:6px; }}
    .dbaide-flabel {{ color:var(--text2); font-size:11px; }}
    .dbaide-chips {{ display:flex; flex-wrap:wrap; gap:6px; max-width:560px; }}
    .dbaide-chip {{ display:inline-flex; align-items:center; gap:5px; padding:4px 10px;
      background:var(--panel2); border:1px solid var(--border); border-radius:14px; color:var(--text);
      font-size:12px; cursor:pointer; user-select:none; }}
    .dbaide-chip:hover {{ border-color:var(--accent); }}
    .dbaide-chip input {{ margin:0; accent-color:var(--accent); }}
    .dbaide-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:14px; }}
    .dbaide-card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px;
      padding:14px 16px; }}
    .dbaide-card-title {{ color:var(--text); font-weight:600; font-size:13px; margin-bottom:10px; }}
    .dbaide-card [data-chart] {{ background:transparent; border:none; min-height:0; }}
    /* declarative 12-column rows */
    .dbaide-row {{ display:grid; grid-template-columns:repeat(12,1fr); gap:14px; margin-bottom:14px; }}
    .dbaide-heading {{ grid-column:span 12; color:var(--text); font-size:15px; font-weight:700; margin:6px 0 0; }}
    .dbaide-kpi {{ display:flex; flex-direction:column; justify-content:center; gap:4px; }}
    .dbaide-kpi-value {{ color:var(--accent); font-size:26px; font-weight:700; line-height:1.1; }}
    .dbaide-kpi-label {{ color:var(--text2); font-size:12px; }}
    .dbaide-table-wrap {{ overflow:auto; max-height:320px; }}
    .dbaide-table {{ width:100%; border-collapse:collapse; font-size:12px; }}
    .dbaide-table th, .dbaide-table td {{ text-align:left; padding:6px 10px;
      border-bottom:1px solid var(--border); color:var(--text); white-space:nowrap; }}
    .dbaide-table th {{ color:var(--text2); font-weight:600; position:sticky; top:0; background:var(--panel); }}
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
