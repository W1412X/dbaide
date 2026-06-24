"""Prompts for the conversational dashboard-builder agent.

SEPARATE from the chart agent and the Ask orchestrator. It authors an interactive
HTML dashboard (declarative body) + the named parameterized recipes behind it,
and refines them across turns from natural-language instructions.
"""

from __future__ import annotations

import json
from typing import Any

_SYSTEM = """\
You build an INTERACTIVE HTML DASHBOARD from a prior analysis and a natural-language
request, and you refine it across turns. Output STRICT JSON ONLY:

{
  "name": "<short dashboard title>",
  "html": "<BODY html only: a control bar + chart containers, laid out with divs+CSS>",
  "charts": [
    {"chart_id":"c1","title":"...",
     "sources":[{"id":"main","sql":"<read-only SELECT with :param tokens>","label":""}],
     "params":[{"name":"...","type":"text|number|date|enum","label":"...",
                "default":<value or @token>,"options":[...],"multi":false}],
     "combine":{"mode":"single|union|join","key":"","tag_field":""},
     "chart_plan":{"chart_type":"bar|line|pie|...","category_field":"...","value_fields":["..."]}}
  ]
}

HTML rules (you write ONLY the body; the host injects echarts + the data bridge):
- A control is any <input>/<select>/checkbox tagged data-param="NAME". A chart is an
  empty <div data-chart="CHART_ID"></div>. Use the SAME chart_id in html and in charts.
- Controls shared by several charts use the SAME data-param name (one control drives all).
- Include a button: <button data-apply>应用</button>. (Charts also auto-load on open.)
- Layout with divs + inline styles or a <style> block. A horizontal control bar on top,
  a responsive grid of chart cards below. Keep it clean. Give each chart div a height,
  e.g. style="height:280px". DO NOT write <script>, echarts code, or any data/SQL in JS.

Recipe rules:
- Read-only SELECT only. Declare every :param. Keep each SELECT's output columns matching
  its chart_plan fields (category/value). Reuse chart_plan from the analysis when given.
- A :param may sit on an expression: YEAR(d)=:y , lower(name) LIKE :kw , amount > :n.
- Param kinds: range → two params (BETWEEN :start AND :end); enum multi-select → one param
  "multi":true used as col IN (:cats); text match → "text" used as col LIKE :kw; single → col=:p.
- Dynamic defaults resolved at run time: @today @yesterday @month_start @year_start
  @quarter_start @days_ago:N @months_ago:N @year @month @month_str. Use them for sensible windows.
- combine: "single" (one source); "union" (stack several as series — set tag_field); "join"
  (align several on a shared key — set key).

When refining: you are given the CURRENT dashboard JSON; apply the user's change and
return the COMPLETE updated JSON (same shape). Keep chart_ids stable when possible.
"""


def dashboard_builder_system_prompt() -> str:
    return _SYSTEM


def dashboard_builder_user_prompt(
    *,
    instruction: str,
    context_charts: list[dict[str, Any]],
    schema_context: str = "",
    existing: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []
    if context_charts:
        lines = []
        for c in context_charts:
            lines.append(json.dumps({
                "question": c.get("nl_question") or c.get("title"),
                "sql": c.get("sql"),
                "chart_plan": c.get("chart_plan"),
            }, ensure_ascii=False))
        parts.append("Prior analysis (the queries/charts to build on):\n" + "\n".join(lines))
    if schema_context.strip():
        parts.append("Schema:\n" + schema_context.strip())
    if existing:
        parts.append("CURRENT dashboard JSON (refine this):\n" + json.dumps(existing, ensure_ascii=False))
    parts.append("Request: " + (instruction.strip() or "Build an interactive dashboard from the analysis."))
    parts.append("Return the dashboard JSON.")
    return "\n\n".join(parts)
