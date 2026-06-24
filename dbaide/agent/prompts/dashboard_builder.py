"""Prompts for the conversational dashboard-builder agent.

SEPARATE from the chart agent and the Ask orchestrator. It authors a DECLARATIVE
dashboard layout (rows of typed tiles) + the named parameterized recipes behind
it — never HTML. The system renders the layout. Refined across turns from NL.
"""

from __future__ import annotations

import json
from typing import Any

_SYSTEM = """\
You design an INTERACTIVE DASHBOARD from a prior analysis and a natural-language
request, and refine it across turns. You do NOT write HTML or CSS — you output a
declarative COMPONENT TREE (like structured pseudocode) plus the data recipes, and
the SYSTEM renders it (themed, styled, responsive). Output STRICT JSON ONLY:

{
  "name": "<short dashboard title>",
  "ui": {
    "type": "page",
    "children": [
      {"type":"row","children":[
        {"type":"kpi","chart":"c_total","span":3,"label":"总销售额"},
        {"type":"kpi","chart":"c_orders","span":3,"label":"订单数"},
        {"type":"chart","chart":"c_trend","span":6,"height":300,"title":"销售趋势"}
      ]},
      {"type":"section","title":"维度分析","children":[
        {"type":"tabs","children":[
          {"type":"tab","label":"按品类","children":[{"type":"chart","chart":"c_cat"}]},
          {"type":"tab","label":"按城市","children":[{"type":"chart","chart":"c_city"}]}
        ]}
      ]},
      {"type":"markdown","text":"> 说明：数据来自销售明细表"},
      {"type":"table","chart":"c_detail","title":"明细"}
    ]
  },
  "charts": [
    {"chart_id":"c1","title":"...",
     "sources":[{"id":"main","sql":"<read-only SELECT with :param tokens>","label":""}],
     "params":[{"name":"...","type":"text|number|date|enum","label":"...",
                "default":<value or @token>,"options":[...],"multi":false}],
     "combine":{"mode":"single|union|join","key":"","tag_field":""},
     "chart_plan":{"chart_type":"bar|line|pie|...","category_field":"...","value_fields":["..."]}}
  ]
}

Layout rules (the "ui" is a nestable component tree — compose freely):
- CONTAINERS (have "children"): "page" (root), "row" (lays children across a 12-column
  grid — give each child a "span" 1-12, summing to ~12), "col"/"stack" (vertical),
  "grid" ("cols": N), "section" ("title" + children), "card" (a bordered box), and
  "tabs" whose children are "tab" nodes ({"label","children"}).
- LEAVES (content): "chart" (an ECharts chart; optional "height", default 280),
  "kpi" (one big metric — its recipe MUST return a single aggregate value, e.g.
  SELECT sum(amt) AS total; give a short "label"), "table" (the recipe's rows),
  "text"/"markdown" (a note; basic markdown), "heading" ("text"), "divider".
- Every chart/kpi/table node MUST reference a "chart" id that exists in "charts".
- Nest to taste: rows inside sections, tabs inside cards, etc. Keep it clean and scannable.
- The filter control bar is generated AUTOMATICALLY from the recipe params (params with
  the same name across charts share one control) — do NOT put controls in the tree.

Recipe rules:
- CRITICAL — use ONLY table and column names that appear in the Schema below. NEVER invent a
  column (e.g. do not assume a numeric "退款率数值" variant exists if the schema only has "退款率").
  Match the exact spelling. If a column is text but you need a number, that's a different chart —
  don't fabricate.
- CRITICAL — for an enum filter, its "options" MUST be copied from that column's listed
  values=[…] in the Schema. NEVER invent filter values (guessing them makes every chart return
  zero rows). If a column has no listed values, don't make it an enum filter.
- CRITICAL — write plain SQL the target engine supports. NO array or dialect-specific functions
  (no arrayLength, no ClickHouse/Postgres-only funcs). Do NOT write optional-filter logic such as
  ":p IS NULL OR arrayLength(:p)=0 OR ...". Write ONE simple predicate per filter:
  col = :p  /  col IN (:cats)  /  col LIKE :kw  /  col >= :n. The system handles binding.
- For enum/multi filters, set "default" to ALL options so the board loads populated.
- Read-only SELECT only. Declare every :param. Keep each SELECT's output columns matching
  its chart_plan fields (category/value). Reuse chart_plan from the analysis when given.
- A KPI tile needs a recipe returning ONE row, ONE aggregate (e.g. SELECT sum(amt) AS total).
- A :param may sit on an expression: lower(name) LIKE :kw , amount > :n.
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
    dialect: str = "",
) -> str:
    parts: list[str] = []
    if dialect:
        parts.append(f"Target SQL engine: {dialect}. Use only SQL/functions it supports.")
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
