"""Single-document HTML for the whole chat transcript (the conversation view).

One QWebEngineView renders the entire conversation — every turn (user bubble,
status chip, agenda, streamed/finalized answer with charts, notes, clarification,
actions). This replaces the per-answer WebEngine views, which each spawned a
Chromium renderer process that QtWebEngine never reclaims (see the project memory
``webengine-process-memory``): memory stays bounded to one view no matter how long
the conversation grows.

GUI-free: this module only builds the HTML/CSS/JS string. The Python side
(``conversation_webview.py``) owns the view, the QWebChannel bridge, and pushes
model updates into ``window.DBChat`` via runJavaScript.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from dbaide.rendering.answer_page import script_json
from dbaide.rendering.vendor_scripts import echarts_script_src, hljs_script_src, marked_script_src


def _theme_vars(theme: Mapping[str, Any]) -> str:
    """:root CSS variables from the app theme payload (every colour is injected so the
    page matches the live theme and adapts when it changes — no hardcoded palette)."""
    t = dict(theme or {})

    def g(key: str, default: str) -> str:
        return str(t.get(key) or default)

    return (
        f"--text:{g('text', '#eef1f5')};--text2:{g('text2', '#b7bec9')};"
        f"--muted:{g('muted', '#737b89')};--bg:{g('bg', '#07080a')};"
        f"--panel:{g('panel', '#111419')};--panel2:{g('panel2', '#151922')};"
        f"--panel3:{g('panel3', '#1b2230')};--border:{g('border', '#1b2026')};"
        f"--code-bg:{g('codeBg', '#090b0f')};--accent:{g('accent', '#3b82f6')};"
        f"--accent-text:{g('accentText', '#ffffff')};--link:{g('link', '#67a7ff')};"
        f"--blue:{g('blue', '#3b82f6')};--green:{g('green', '#55c985')};"
        f"--yellow:{g('yellow', '#e9c46a')};--red:{g('red', '#ff6b6b')};"
    )


# The whole conversation controller. window.DBChat is the API the Python side drives
# via runJavaScript; the bridge (QWebChannel) carries user interactions back to Python.
_CONVERSATION_JS = r"""
(function(){
  'use strict';
  var bridge = null;
  var turns = [];                 // ordered list of turn models
  var byId = {};                  // id -> {model, node}
  var followBottom = true;        // tail-follow during streaming
  var hintEl = null;

  // ---- bridge (optional; absent during pure-render tests) --------------------
  function initBridge(){
    if (typeof QWebChannel === 'undefined' || !window.qt) return;
    try {
      new QWebChannel(qt.webChannelTransport, function(ch){ bridge = ch.objects.bridge; });
    } catch(e){}
  }
  function call(method){
    if (!bridge || typeof bridge[method] !== 'function') return;
    try { bridge[method].apply(bridge, Array.prototype.slice.call(arguments, 1)); } catch(e){}
  }

  // ---- markdown + charts (shared with the answer page) -----------------------
  function configureMarked(){
    if (!window.marked) return;
    if (typeof marked.use === 'function') marked.use({ breaks: true, gfm: true });
    marked.setOptions({ highlight: function(code, lang){
      if (window.hljs){
        try {
          if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, {language: lang}).value;
          return hljs.highlightAuto(code).value;
        } catch(e){}
      }
      return code;
    }});
  }
  function renderMarkdown(source){
    var sec = document.createElement('section');
    sec.className = 'md-block';
    if (!window.marked){ sec.textContent = source; return sec; }
    try { sec.innerHTML = marked.parse(String(source||'')); } catch(e){ sec.textContent = source; }
    if (window.hljs && typeof hljs.highlightElement === 'function'){
      sec.querySelectorAll('pre code').forEach(function(el){ try { hljs.highlightElement(el); } catch(e){} });
    }
    return sec;
  }
  function compactAxisValue(value){
    var n = Number(value);
    if (!Number.isFinite(n)) return value;
    var abs = Math.abs(n);
    if (abs >= 1e8) return (n/1e8).toFixed(1).replace(/\.0$/,'') + '亿';
    if (abs >= 1e4) return (n/1e4).toFixed(1).replace(/\.0$/,'') + '万';
    if (abs >= 1000) return (n/1000).toFixed(1).replace(/\.0$/,'') + 'k';
    if (Math.abs(n - Math.round(n)) < 1e-6) return String(Math.round(n));
    return n.toFixed(2).replace(/0+$/,'').replace(/\.$/,'');
  }
  function applyRuntimeFormatters(root){
    var patch = function(axis){
      if (!axis || axis.type !== 'value') return;
      axis.axisLabel = axis.axisLabel || {};
      if (axis._compactValues){
        if (axis._valueFormat === 'currency') axis.axisLabel.formatter = function(v){ return '$' + compactAxisValue(v); };
        else if (axis._valueFormat !== 'percent') axis.axisLabel.formatter = compactAxisValue;
        delete axis._compactValues; delete axis._valueFormat;
      }
      if (axis.name){
        axis.nameGap = Math.max(axis.nameGap || 0, 14);
        axis.nameLocation = axis.nameLocation || 'end';
        axis.nameTruncate = { maxWidth: 96, ellipsis: '…' };
      }
    };
    var ys = Array.isArray(root.yAxis) ? root.yAxis : [root.yAxis]; ys.forEach(patch);
    var xs = Array.isArray(root.xAxis) ? root.xAxis : [root.xAxis];
    xs.forEach(function(a){ if (a && a.type === 'value') patch(a); });
  }
  function renderChart(block){
    var wrap = document.createElement('section');
    wrap.className = 'chart-block';
    var title = String(block.title || '').trim();
    if (title){ var h = document.createElement('h3'); h.className = 'chart-title'; h.textContent = title; wrap.appendChild(h); }
    var canvas = document.createElement('div');
    canvas.className = 'chart-canvas';
    canvas.style.height = Math.max(240, Number(block.height) || 320) + 'px';
    wrap.appendChild(canvas);
    if (!window.echarts){ var e = document.createElement('div'); e.className = 'chart-error'; e.textContent = 'ECharts failed to load.'; wrap.appendChild(e); return wrap; }
    var option = block.echarts_option || {};
    try { applyRuntimeFormatters(option); } catch(e){}
    try {
      var chart = echarts.init(canvas, null, { renderer: 'canvas' });
      chart.setOption(option, true);
      var resize = function(){ try { chart.resize(); } catch(e){} };
      window.addEventListener('resize', resize);
      if (window.ResizeObserver) new ResizeObserver(resize).observe(canvas);
      setTimeout(resize, 0);
    } catch(e){ var er = document.createElement('div'); er.className = 'chart-error'; er.textContent = 'Chart render failed.'; wrap.appendChild(er); }
    return wrap;
  }
  function renderBlocks(blocks){
    var frag = document.createDocumentFragment();
    (blocks || []).forEach(function(b){
      if (!b || typeof b !== 'object') return;
      if (b.type === 'markdown') frag.appendChild(renderMarkdown(String(b.source || '')));
      else if (b.type === 'chart') frag.appendChild(renderChart(b));
    });
    return frag;
  }

  // ---- per-turn sub-renderers ------------------------------------------------
  function el(cls, tag){ var e = document.createElement(tag || 'div'); if (cls) e.className = cls; return e; }

  function renderUser(turn){
    var u = turn.user;
    if (!u || (!u.text && !(u.attachments||[]).length)) return null;
    var row = el('dbc-user');
    if (u.meta){ var m = el('dbc-meta'); m.textContent = u.meta; row.appendChild(m); }
    if ((u.attachments||[]).length){
      var tags = el('dbc-tags');
      u.attachments.forEach(function(a){ var t = el('dbc-tag'); t.textContent = a.name || ''; tags.appendChild(t); });
      row.appendChild(tags);
    }
    if (u.text){ var b = el('dbc-bubble'); b.textContent = u.text; row.appendChild(b); }
    return row;
  }

  function renderStatus(turn){
    var s = turn.status;
    if (!s) return null;
    var chip = el('dbc-status dbc-status-' + (s.state || 'done'));
    if (s.state === 'running' || s.state === 'waiting'){
      if (s.state === 'running') chip.appendChild(el('dbc-spinner'));
      var t = el('dbc-status-text'); t.textContent = s.phase || ''; chip.appendChild(t);
    } else {
      var label = s.state === 'error' ? (s.phase || 'Failed') : (s.steps ? ('View agent trace · ' + s.steps + ' steps') : 'View agent trace');
      var lt = el('dbc-status-text'); lt.textContent = label; chip.appendChild(lt);
      chip.style.cursor = 'pointer';
      chip.onclick = function(){ call('toggleTrace', String(turn.id)); };
    }
    return chip;
  }

  var AGENDA_GLYPH = { done:'✓', in_progress:'●', dropped:'–', pending:'○' };
  function renderAgenda(turn){
    var items = turn.agenda || [];
    if (!items.length) return null;
    var box = el('dbc-agenda');
    var done = items.filter(function(i){ return i.status === 'done'; }).length;
    var head = el('dbc-agenda-head'); head.textContent = 'Agenda · ' + done + ' of ' + items.length; box.appendChild(head);
    items.forEach(function(i){
      var row = el('dbc-agenda-row');
      var g = el('dbc-agenda-glyph dbc-ag-' + (i.status || 'pending')); g.textContent = AGENDA_GLYPH[i.status] || '○'; row.appendChild(g);
      var txt = el('dbc-agenda-text');
      var ti = el('dbc-agenda-title', 'span'); ti.textContent = i.title || ''; txt.appendChild(ti);
      var sub = [i.kind, i.acceptance].filter(Boolean).join(' · ');
      if (sub){ var su = el('dbc-agenda-sub', 'span'); su.textContent = sub; txt.appendChild(su); }
      row.appendChild(txt); box.appendChild(row);
    });
    return box;
  }

  function renderNotes(turn){
    var notes = turn.notes || [];
    if (!notes.length) return null;
    var frag = document.createDocumentFragment();
    notes.forEach(function(n){
      var box = el('dbc-note dbc-note-' + (n.kind || 'warning'));
      box.appendChild(renderMarkdown(String(n.text || '')));
      frag.appendChild(box);
    });
    return frag;
  }

  function renderClarification(turn){
    var c = turn.clarification;
    if (!c || c.done) return null;
    var box = el('dbc-clarify');
    var qs = c.mode === 'multi' ? (c.questions || []) : [{ question: c.question, options: c.options || [] }];
    var idx = c._step || 0;
    var cur = qs[idx] || {};
    if (c.mode === 'multi'){ var prog = el('dbc-clarify-prog'); prog.textContent = 'Question ' + (idx+1) + ' of ' + qs.length; box.appendChild(prog); }
    if (cur.question){ var q = el('dbc-clarify-q'); q.textContent = cur.question; box.appendChild(q); }
    var opts = el('dbc-clarify-opts');
    (cur.options || []).forEach(function(opt){
      var chip = el('dbc-chip'); chip.textContent = opt;
      chip.onclick = function(){ submitClarify(turn, c, qs, idx, opt); };
      opts.appendChild(chip);
    });
    box.appendChild(opts);
    var row = el('dbc-clarify-row');
    var input = el('dbc-clarify-input', 'input'); input.type = 'text'; input.placeholder = 'Type a reply…';
    input.onkeydown = function(ev){ if (ev.key === 'Enter' && input.value.trim()) submitClarify(turn, c, qs, idx, input.value.trim()); };
    row.appendChild(input);
    var btn = el('dbc-btn dbc-btn-primary', 'button');
    btn.textContent = (c.mode === 'multi' && idx < qs.length - 1) ? 'Next' : 'Send';
    btn.onclick = function(){ if (input.value.trim()) submitClarify(turn, c, qs, idx, input.value.trim()); };
    row.appendChild(btn);
    box.appendChild(row);
    return box;
  }
  function submitClarify(turn, c, qs, idx, value){
    if (c.mode === 'multi'){
      c._answers = c._answers || [];
      c._answers[idx] = value;
      if (idx < qs.length - 1){ c._step = idx + 1; rerenderTurn(turn); return; }
      var joined = c._answers.map(function(a, i){ return (i+1) + '. ' + (a || ''); }).join('\n');
      c.done = true; rerenderTurn(turn); call('clarify', String(turn.id), joined); return;
    }
    c.done = true; rerenderTurn(turn); call('clarify', String(turn.id), value);
  }

  function renderActions(turn){
    var actions = turn.actions || [];
    if (!actions.length) return null;
    var row = el('dbc-actions');
    actions.forEach(function(a){
      var btn = el('dbc-btn' + (a.kind === 'primary' ? ' dbc-btn-primary' : ''), 'button');
      btn.textContent = a.label || '';
      btn.onclick = function(){ call('action', String(turn.id), String(a.id || '')); };
      row.appendChild(btn);
    });
    return row;
  }

  function renderTurnInto(node, turn){
    node.innerHTML = '';
    node.className = 'dbc-turn';
    var add = function(x){ if (x) node.appendChild(x); };
    add(renderUser(turn));
    add(renderStatus(turn));
    add(renderAgenda(turn));
    var content = el('dbc-content');
    if (turn.blocks && turn.blocks.length){
      content.appendChild(renderBlocks(turn.blocks));
    } else if (turn.stream != null){
      var pre = el('dbc-stream'); pre.textContent = turn.stream; content.appendChild(pre);
    }
    var notes = renderNotes(turn); if (notes) content.appendChild(notes);
    var clar = renderClarification(turn); if (clar) content.appendChild(clar);
    if (content.childNodes.length) add(content);
    add(renderActions(turn));
  }

  function rerenderTurn(turn){
    var entry = byId[turn.id];
    if (!entry) return;
    renderTurnInto(entry.node, turn);
    scrollIfFollowing();
  }

  // ---- scrolling -------------------------------------------------------------
  function atBottom(){ return (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 24); }
  function scrollIfFollowing(){ if (followBottom) window.scrollTo(0, document.body.scrollHeight); }
  window.addEventListener('scroll', function(){ followBottom = atBottom(); });

  // ---- public API (driven by Python via runJavaScript) -----------------------
  function root(){ return document.getElementById('dbc-root'); }
  function showHint(text){
    if (!hintEl){ hintEl = el('dbc-hint'); root().appendChild(hintEl); }
    hintEl.textContent = text || '';
    hintEl.style.display = text ? '' : 'none';
  }
  var DBChat = {
    render: function(list){
      turns = list || []; byId = {};
      var r = root(); r.innerHTML = ''; hintEl = null;
      turns.forEach(function(t){
        var node = el('dbc-turn'); byId[t.id] = { model: t, node: node };
        renderTurnInto(node, t); r.appendChild(node);
      });
      followBottom = true; scrollIfFollowing();
    },
    setTurn: function(turn){
      if (!turn || turn.id == null) return;
      var entry = byId[turn.id];
      if (entry){ entry.model = turn; renderTurnInto(entry.node, turn); }
      else {
        var node = el('dbc-turn'); byId[turn.id] = { model: turn, node: node };
        turns.push(turn); renderTurnInto(node, turn); root().appendChild(node);
      }
      scrollIfFollowing();
    },
    appendStream: function(id, text){
      var entry = byId[id]; if (!entry) return;
      var m = entry.model; m.stream = (m.stream || '') + text;
      var pre = entry.node.querySelector('.dbc-stream');
      if (pre){ pre.appendChild(document.createTextNode(text)); } else { renderTurnInto(entry.node, m); }
      scrollIfFollowing();
    },
    setStatus: function(id, status){ var e = byId[id]; if (!e) return; e.model.status = status; rerenderTurn(e.model); },
    setAgenda: function(id, items){ var e = byId[id]; if (!e) return; e.model.agenda = items || []; rerenderTurn(e.model); },
    setHint: showHint,
    clearAll: function(){ turns = []; byId = {}; hintEl = null; root().innerHTML = ''; followBottom = true; },
    setTheme: function(theme){
      var rs = document.documentElement.style;
      for (var k in (theme || {})){ if (Object.prototype.hasOwnProperty.call(theme, k)) rs.setProperty('--' + k, theme[k]); }
    }
  };
  window.DBChat = DBChat;

  function boot(){
    configureMarked();
    initBridge();
    if (window.__DBC_INITIAL__){ try { DBChat.render(window.__DBC_INITIAL__); } catch(e){} }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
"""


def _base_css() -> str:
    return r"""
    html, body { margin:0; padding:0; background:var(--bg); color:var(--text);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size:13px; line-height:1.55; }
    #dbc-root { padding:16px 28px 28px; display:flex; flex-direction:column; gap:18px; }
    .dbc-turn { display:flex; flex-direction:column; gap:8px; }
    .dbc-user { display:flex; flex-direction:column; align-items:flex-end; gap:4px; }
    .dbc-meta { color:var(--muted); font-size:11px; }
    .dbc-tags { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }
    .dbc-tag { background:var(--panel2); border:1px solid var(--border); color:var(--text2);
      border-radius:8px; padding:2px 8px; font-size:11px; }
    .dbc-bubble { background:var(--panel2); border:1px solid var(--border); color:var(--text);
      border-radius:10px; padding:9px 12px; max-width:620px; white-space:pre-wrap; word-break:break-word; }
    .dbc-status { display:inline-flex; align-items:center; gap:8px; align-self:flex-start;
      padding:4px 10px; border-radius:8px; font-size:12px; color:var(--muted); }
    .dbc-status-running .dbc-status-text { color:var(--blue); }
    .dbc-status-waiting .dbc-status-text { color:var(--yellow); }
    .dbc-status-error .dbc-status-text { color:var(--red); }
    .dbc-status-done:hover { background:var(--panel2); }
    .dbc-status-done:hover .dbc-status-text { color:var(--text); }
    .dbc-spinner { width:13px; height:13px; border-radius:50%; border:2px solid var(--border);
      border-top-color:var(--blue); animation:dbc-spin .7s linear infinite; }
    @keyframes dbc-spin { to { transform:rotate(360deg); } }
    .dbc-agenda { background:var(--panel); border:1px solid var(--border); border-radius:8px;
      padding:8px 12px; align-self:flex-start; min-width:240px; }
    .dbc-agenda-head { color:var(--text2); font-size:10px; font-weight:600; letter-spacing:.3px; margin-bottom:4px; }
    .dbc-agenda-row { display:flex; gap:8px; align-items:baseline; margin:2px 0; }
    .dbc-agenda-glyph { font-size:12px; font-weight:700; }
    .dbc-ag-done { color:var(--green); } .dbc-ag-in_progress { color:var(--blue); }
    .dbc-ag-dropped { color:var(--yellow); } .dbc-ag-pending { color:var(--muted); }
    .dbc-agenda-title { color:var(--text); font-size:12px; font-weight:600; }
    .dbc-agenda-sub { color:var(--muted); font-size:10px; margin-left:6px; }
    .dbc-content { display:flex; flex-direction:column; gap:6px; }
    .dbc-stream { white-space:pre-wrap; word-break:break-word; color:var(--text); margin:0; font:inherit; }
    .dbc-note { border-radius:8px; padding:6px 12px; }
    .dbc-note-error { background:rgba(255,107,107,.08); border:1px solid var(--red); }
    .dbc-note-warning { background:var(--panel); border:1px solid var(--border); }
    .dbc-clarify { background:var(--panel); border:1px solid var(--border); border-radius:8px;
      padding:12px; display:flex; flex-direction:column; gap:8px; align-self:flex-start; min-width:320px; }
    .dbc-clarify-prog { color:var(--muted); font-size:11px; font-weight:700; }
    .dbc-clarify-q { color:var(--text); font-size:13px; }
    .dbc-clarify-opts { display:flex; flex-direction:column; gap:6px; }
    .dbc-chip { background:var(--panel2); border:1px solid var(--border); color:var(--text);
      border-radius:8px; padding:7px 12px; cursor:pointer; font-size:13px; }
    .dbc-chip:hover { background:var(--panel3); }
    .dbc-clarify-row { display:flex; gap:8px; }
    .dbc-clarify-input { flex:1; background:var(--bg); border:1px solid var(--border); color:var(--text);
      border-radius:6px; padding:6px 10px; font:inherit; }
    .dbc-actions { display:flex; gap:8px; align-self:flex-end; }
    .dbc-btn { background:var(--panel2); border:1px solid var(--border); color:var(--text);
      border-radius:6px; padding:6px 12px; cursor:pointer; font:inherit; }
    .dbc-btn-primary { background:var(--accent); color:var(--accent-text); border-color:var(--accent); }
    .dbc-btn:hover { filter:brightness(1.1); }
    .dbc-hint { color:var(--muted); font-size:13px; padding:8px 0; }
    /* markdown + chart blocks (shared look with the answer page) */
    .md-block > :first-child { margin-top:0; } .md-block > :last-child { margin-bottom:0; }
    .md-block h1 { font-size:18px; font-weight:700; margin:12px 0 6px; }
    .md-block h2 { font-size:16px; font-weight:700; margin:10px 0 6px; }
    .md-block h3 { font-size:14px; font-weight:600; margin:9px 0 4px; }
    .md-block p { margin:5px 0; } .md-block ul, .md-block ol { margin:4px 0; padding-left:1.35em; }
    .md-block li { margin:2px 0; }
    .md-block blockquote { margin:6px 0; padding:6px 12px; background:var(--panel2); color:var(--text2);
      border-left:3px solid var(--border); }
    .md-block a { color:var(--link); text-decoration:none; } .md-block a:hover { text-decoration:underline; }
    .md-block code { background:var(--code-bg); padding:1px 5px; border-radius:4px;
      font-family:Menlo, Monaco, Consolas, monospace; font-size:12px; }
    .md-block pre { background:var(--code-bg); border:1px solid var(--border); border-radius:8px;
      padding:10px 12px; overflow-x:auto; margin:8px 0; }
    .md-block pre code { background:transparent; padding:0; }
    .md-block table { border-collapse:collapse; margin:8px 0; width:100%; }
    .md-block th, .md-block td { border-bottom:1px solid var(--border); padding:7px 14px 7px 0;
      text-align:left; vertical-align:top; }
    .md-block th { color:var(--muted); font-weight:600; }
    .md-block .hljs { background:var(--code-bg); color:var(--text); }
    .chart-block { margin:12px 0; } .chart-title { margin:0 0 4px; font-size:13px; font-weight:600; color:var(--text); }
    .chart-canvas { width:100%; min-height:240px; } .chart-error { color:var(--red); font-size:13px; padding:8px 0; }
    """


def build_conversation_page(
    theme: Mapping[str, Any] | None = None,
    *,
    marked_src: str | None = None,
    hljs_src: str | None = None,
    echarts_src: str | None = None,
    initial_turns: list[dict[str, Any]] | None = None,
    qwebchannel: bool = True,
) -> str:
    """Build the single HTML document that renders the whole conversation.

    ``initial_turns`` (optional) are embedded for first paint — used by session
    restore so the page shows immediately without a round-trip. Further updates are
    pushed by Python via ``window.DBChat`` over runJavaScript / the bridge.
    """
    theme = dict(theme or {})
    marked = str(marked_src if marked_src is not None else marked_script_src())
    hljs = str(hljs_src if hljs_src is not None else hljs_script_src())
    echarts = str(echarts_src if echarts_src is not None else echarts_script_src())
    marked_json = json.dumps(marked, ensure_ascii=False)
    hljs_json = json.dumps(hljs, ensure_ascii=False)
    echarts_json = json.dumps(echarts, ensure_ascii=False)
    initial_json = script_json(list(initial_turns or []))
    channel = '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>' if qwebchannel else ""
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{ {_theme_vars(theme)} }}
    {_base_css()}
  </style>
  {channel}
  <script src={marked_json}></script>
  <script src={hljs_json}></script>
  <script src={echarts_json}></script>
</head>
<body>
  <div id="dbc-root"></div>
  <script>window.__DBC_INITIAL__ = {initial_json};</script>
  <script>{_CONVERSATION_JS}</script>
</body>
</html>"""
