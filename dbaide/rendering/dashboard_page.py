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
  var bridge=null, ready=false, q=[], cache=null, lastParams={}, pending={}, tok=0;
  function whenReady(fn){ ready?fn():q.push(fn); }
  function init(){
    if(typeof QWebChannel==='undefined' || !window.qt){ return; }
    new QWebChannel(qt.webChannelTransport, function(ch){
      bridge=ch.objects.bridge; ready=true;
      // async results arrive by token (the SQL runs off the GUI thread → no freeze)
      bridge.resultReady.connect(function(token, payload){
        var resolve=pending[token]; if(!resolve) return; delete pending[token];
        var r=null; try{ r=JSON.parse(payload); }catch(e){} resolve(r);
      });
      q.forEach(function(f){f();}); q=[]; refresh();
    });
  }
  function query(cid, params){
    return new Promise(function(resolve){
      var token='t'+(++tok); pending[token]=resolve;
      bridge.request(token, cid, JSON.stringify(params||{}));
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
  function numColumns(cols, rows){
    return cols.filter(function(c){ var seen=false;
      for(var i=0;i<rows.length;i++){ var x=rows[i][c]; if(x==null) continue; if(typeof x!=='number') return false; seen=true; }
      return seen; });
  }
  function fmtVal(v, fmt){
    if(v==null) return '—';
    if(typeof v!=='number') return esc(v);
    if(fmt==='percent') return (Math.round(v*10)/10).toLocaleString()+'%';
    if(fmt==='currency') return '¥'+Math.round(v).toLocaleString();
    if(fmt==='int') return Math.round(v).toLocaleString();
    return v.toLocaleString();
  }
  function kpiSeries(res){
    var rows=res.rows||[], cols=res.columns||[];
    if(!rows.length || !cols.length) return null;
    var nums=numColumns(cols, rows), col=nums.length?nums[nums.length-1]:cols[cols.length-1];
    return rows.map(function(r){ return r[col]; });
  }
  function sparkSvg(vals){
    var nums=vals.filter(function(x){return typeof x==='number';});
    if(nums.length<2) return '';
    var min=Math.min.apply(null,nums), max=Math.max.apply(null,nums), rng=(max-min)||1, w=120,h=34,n=nums.length;
    var pts=nums.map(function(v,i){ var x=(i/(n-1))*w, y=h-((v-min)/rng)*(h-4)-2; return x.toFixed(1)+','+y.toFixed(1); }).join(' ');
    return '<svg viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none"><polyline fill="none" stroke="var(--accent)" stroke-width="2" points="'+pts+'"/></svg>';
  }
  function renderKpi(card, res){
    var fmt=card.getAttribute('data-format')||'', trend=card.getAttribute('data-trend')==='1';
    var valEl=card.querySelector('.dbaide-kpi-value'), sparkEl=card.querySelector('.dbaide-kpi-spark');
    var vals=kpiSeries(res);
    if(!vals){ valEl.textContent='—'; if(sparkEl) sparkEl.innerHTML=''; return; }
    var cur=vals[vals.length-1], html=fmtVal(cur, fmt);
    if(trend){
      var nums=vals.filter(function(x){return typeof x==='number';});
      if(nums.length>=2){ var prev=nums[nums.length-2], d=(prev===0)?0:((cur-prev)/Math.abs(prev))*100;
        var cls=d>=0?'up':'down', arr=d>=0?'▲':'▼';
        html+=' <span class="dbaide-kpi-delta '+cls+'">'+arr+Math.abs(Math.round(d*10)/10)+'%</span>'; }
      if(sparkEl) sparkEl.innerHTML=sparkSvg(vals);
    } else if(sparkEl){ sparkEl.innerHTML=''; }
    valEl.innerHTML=html;
  }
  function renderTable(el, res){
    var cols=res.columns||[], rows=(res.rows||[]).slice(0,200), nums=numColumns(cols, rows);
    function draw(sortCol, dir){
      var rr=rows.slice();
      if(sortCol!=null){ rr.sort(function(a,b){ var x=a[sortCol],y=b[sortCol];
        if(x==null) return 1; if(y==null) return -1; if(x<y) return -dir; if(x>y) return dir; return 0; }); }
      var head='<tr>'+cols.map(function(c){ var isn=nums.indexOf(c)>=0, sc=(c===sortCol)?' sorted':'', arrow=(c===sortCol)?(dir>0?'▲':'▼'):'';
        return '<th class="'+(isn?'num':'')+sc+'" data-col="'+esc(c)+'" data-arrow="'+arrow+'">'+esc(c)+'</th>'; }).join('')+'</tr>';
      var body=rr.slice(0,100).map(function(r){ return '<tr>'+cols.map(function(c){ var isn=nums.indexOf(c)>=0;
        return '<td class="'+(isn?'num':'')+'">'+(isn?fmtVal(r[c],''):esc(r[c]))+'</td>'; }).join('')+'</tr>'; }).join('');
      el.innerHTML='<table class="dbaide-table"><thead>'+head+'</thead><tbody>'+body+'</tbody></table>';
      el.querySelectorAll('th').forEach(function(th){ th.addEventListener('click', function(){
        var c=th.getAttribute('data-col'); draw(c, (c===sortCol && dir>0)?-1:1); }); });
    }
    draw(null, 1);
  }
  function cachedQuery(cid, params){
    // dedup within a refresh: one recipe feeding kpi+chart+table runs its SQL once
    if(!cache) return query(cid, params);
    var k=cid+'|'+JSON.stringify(params||{});
    return cache[k] || (cache[k]=query(cid, params));
  }
  function isEmptyTile(el, kind){
    if(kind==='kpi'){ var v=el.querySelector('.dbaide-kpi-value'); return !v || v.textContent==='…' || v.textContent===''; }
    if(kind==='table') return !el.querySelector('table');
    return !el.querySelector('canvas');
  }
  function showBusy(el){
    if(el.querySelector(':scope > .dbaide-busy')) return;
    var o=document.createElement('div'); o.className='dbaide-busy'; o.innerHTML='<span class="dbaide-spin"></span>';
    el.appendChild(o);
  }
  function clearBusy(el){ var o=el.querySelector(':scope > .dbaide-busy'); if(o && o.parentNode) o.parentNode.removeChild(o); }
  function markLoading(el, kind){
    if(!isEmptyTile(el, kind)){ showBusy(el); return; }   // refresh/apply: overlay spinner over existing content
    if(kind==='kpi'){ var v=el.querySelector('.dbaide-kpi-value'); if(v) v.innerHTML='<span class="dbaide-spin"></span>'; return; }
    el.classList.remove('dbaide-empty'); el.classList.add('dbaide-loading'); el.innerHTML='';
  }
  function renderTile(el, params){
    if(el.closest('.dbaide-tabpanel:not(.active)')) return;   // render lazily when its tab opens
    var cid=el.getAttribute('data-chart'), kind=el.getAttribute('data-kind')||'chart';
    markLoading(el, kind);
    cachedQuery(cid, params).then(function(res){
      clearBusy(el);   // remove the apply/refresh overlay once new data lands
      var err=(!res || res.error);
      if(kind==='kpi'){   // a card with child nodes — never wipe it with textContent
        if(err){ var v=el.querySelector('.dbaide-kpi-value'); if(v) v.textContent='—'; return; }
        renderKpi(el, res); return;
      }
      el.classList.remove('dbaide-loading');
      if(err){ el.classList.add('dbaide-empty'); el.textContent=(res&&res.error)?res.error:'无数据'; return; }
      el.classList.remove('dbaide-empty');
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
      lastParams=collectParams();
      cache={};   // fresh dedup cache per refresh (params are constant within one)
      document.querySelectorAll('[data-chart]').forEach(function(el){ renderTile(el, lastParams); });
    });
  }
  function activateTab(btn){
    var key=btn.getAttribute('data-tab'), bar=btn.parentElement, tabs=bar.parentElement;
    bar.querySelectorAll('.dbaide-tab').forEach(function(b){ b.classList.toggle('active', b===btn); });
    tabs.querySelectorAll(':scope > .dbaide-tabpanel').forEach(function(p){
      p.classList.toggle('active', p.getAttribute('data-tabpanel')===key);
    });
    var panel=tabs.querySelector('.dbaide-tabpanel.active');
    if(panel){ panel.querySelectorAll('[data-chart]').forEach(function(el){ renderTile(el, lastParams); }); }
  }
  function updateSummaries(){
    document.querySelectorAll('.dbaide-dd').forEach(function(d){
      var s=d.querySelector('summary'); if(!s) return;
      var lbl=s.getAttribute('data-ddlabel')||'';
      var boxes=d.querySelectorAll('input[type="checkbox"]'), n=0;
      boxes.forEach(function(b){ if(b.checked) n++; });
      s.textContent=lbl+' ('+n+'/'+boxes.length+')';   // ▾ comes from ::after
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
    document.querySelectorAll('.dbaide-tab').forEach(function(b){
      b.addEventListener('click', function(e){ e.preventDefault(); activateTab(b); });
    });
    document.querySelectorAll('[data-ckall],[data-ckno]').forEach(function(b){
      b.addEventListener('click', function(e){ e.preventDefault();
        var on=b.hasAttribute('data-ckall'), box=b.closest('.dbaide-checklist');
        box.querySelectorAll('input[type="checkbox"]').forEach(function(c){ c.checked=on; });
        updateSummaries();
      });
    });
    document.addEventListener('change', updateSummaries);
    updateSummaries();
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
    [data-kind="chart"] {{ width:100%; min-height:260px; background:var(--panel); border:1px solid var(--border);
      border-radius:8px; }}
    .dbaide-empty {{ display:flex; align-items:center; justify-content:center;
      color:var(--muted); min-height:120px; }}
    @keyframes dbaide-rot {{ to {{ transform:rotate(360deg); }} }}
    .dbaide-loading {{ display:flex; align-items:center; justify-content:center; min-height:120px; }}
    .dbaide-spin, .dbaide-loading::after {{ content:''; display:inline-block; width:22px; height:22px;
      border:2px solid var(--border); border-top-color:var(--accent); border-radius:50%;
      animation:dbaide-rot .7s linear infinite; }}
    .dbaide-card, [data-kind="chart"], .dbaide-table-wrap {{ position:relative; }}
    .dbaide-busy {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
      background:rgba(0,0,0,.28); border-radius:8px; z-index:5; }}
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
    .dbaide-controls > label, .dbaide-field {{ display:flex; flex-direction:column; gap:6px; }}
    .dbaide-flabel {{ color:var(--text2); font-size:11px; }}
    /* compact collapsible multi-select dropdown */
    .dbaide-dd {{ position:relative; }}
    .dbaide-dd > summary {{ list-style:none; cursor:pointer; padding:7px 12px; min-width:130px;
      background:var(--panel2); border:1px solid var(--border); border-radius:6px; color:var(--text);
      font-size:12px; user-select:none; white-space:nowrap; }}
    .dbaide-dd > summary::-webkit-details-marker {{ display:none; }}
    .dbaide-dd > summary::after {{ content:" ▾"; color:var(--muted); }}
    .dbaide-dd[open] > summary {{ border-color:var(--accent); }}
    .dbaide-checklist {{ position:absolute; z-index:50; top:calc(100% + 4px); left:0; min-width:190px;
      max-height:280px; overflow:auto; background:var(--panel); border:1px solid var(--border);
      border-radius:8px; padding:6px; display:flex; flex-direction:column; gap:1px;
      box-shadow:0 10px 28px rgba(0,0,0,.45); }}
    .dbaide-check {{ display:flex; align-items:center; gap:8px; padding:5px 8px; border-radius:5px;
      color:var(--text); font-size:12px; cursor:pointer; white-space:nowrap; }}
    .dbaide-check:hover {{ background:var(--panel2); }}
    .dbaide-check input {{ margin:0; accent-color:var(--accent); }}
    .dbaide-ckbar {{ display:flex; gap:6px; padding:2px 4px 6px; position:sticky; top:0;
      background:var(--panel); border-bottom:1px solid var(--border); margin-bottom:4px; }}
    .dbaide-ckbar button {{ background:transparent; color:var(--accent); border:none; padding:2px 6px;
      font-size:11px; font-weight:600; cursor:pointer; }}
    .dbaide-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:14px; }}
    .dbaide-card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px;
      padding:14px 16px; }}
    .dbaide-card-title {{ color:var(--text); font-weight:600; font-size:13px; margin-bottom:10px; }}
    .dbaide-card [data-chart] {{ background:transparent; border:none; min-height:0; }}
    /* declarative component tree */
    .dbaide-row {{ display:grid; grid-template-columns:repeat(12,1fr); gap:14px; margin-bottom:14px; align-items:stretch; }}
    .dbaide-cell {{ min-width:0; }}
    .dbaide-cell > * {{ height:100%; }}
    .dbaide-grid2 {{ display:grid; gap:14px; margin-bottom:14px; }}
    .dbaide-section {{ margin-bottom:14px; }}
    .dbaide-text {{ color:var(--text2); font-size:13px; line-height:1.6; margin:4px 0 14px; }}
    .dbaide-text h2,.dbaide-text h3,.dbaide-text h4 {{ color:var(--text); margin:6px 0; }}
    .dbaide-text code {{ background:var(--panel2); padding:1px 5px; border-radius:4px; }}
    .dbaide-text blockquote {{ margin:4px 0; padding-left:10px; border-left:3px solid var(--border); color:var(--muted); }}
    .dbaide-divider {{ border:none; border-top:1px solid var(--border); margin:6px 0 16px; }}
    /* tabs */
    .dbaide-tabs {{ margin-bottom:14px; }}
    .dbaide-tabbar {{ display:flex; gap:4px; border-bottom:1px solid var(--border); margin-bottom:12px; }}
    .dbaide-tab {{ background:transparent; color:var(--text2); border:none; border-bottom:2px solid transparent;
      border-radius:0; padding:8px 14px; font-weight:600; cursor:pointer; }}
    .dbaide-tab:hover {{ color:var(--text); filter:none; }}
    .dbaide-tab.active {{ color:var(--accent); border-bottom-color:var(--accent); }}
    .dbaide-tabpanel {{ display:none; }}
    .dbaide-tabpanel.active {{ display:block; }}
    .dbaide-heading {{ color:var(--text); font-size:15px; font-weight:700; margin:6px 0 10px; }}
    .dbaide-kpi {{ display:flex; flex-direction:column; justify-content:center; gap:4px; }}
    .dbaide-kpi-value {{ color:var(--accent); font-size:26px; font-weight:700; line-height:1.1; }}
    .dbaide-kpi-delta {{ font-size:12px; font-weight:600; margin-left:8px; }}
    .dbaide-kpi-delta.up {{ color:#22c55e; }} .dbaide-kpi-delta.down {{ color:#ef4444; }}
    .dbaide-kpi-label {{ color:var(--text2); font-size:12px; }}
    .dbaide-kpi-spark {{ margin-top:6px; height:34px; }}
    .dbaide-kpi-spark svg {{ width:100%; height:34px; display:block; }}
    .dbaide-table-wrap {{ overflow:auto; max-height:320px; }}
    .dbaide-table {{ width:100%; border-collapse:collapse; font-size:12px; }}
    .dbaide-table th, .dbaide-table td {{ text-align:left; padding:6px 10px;
      border-bottom:1px solid var(--border); color:var(--text); white-space:nowrap; }}
    .dbaide-table th {{ color:var(--text2); font-weight:600; position:sticky; top:0; background:var(--panel);
      cursor:pointer; user-select:none; }}
    .dbaide-table th:hover {{ color:var(--text); }}
    .dbaide-table th.sorted::after {{ content:attr(data-arrow); color:var(--accent); margin-left:4px; }}
    .dbaide-table td.num, .dbaide-table th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
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
