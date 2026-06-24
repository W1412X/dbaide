"""Prompts for the dashboard-compiler agent.

This agent is SEPARATE from the chart agent (which shapes rows into a chart) and
from the Ask orchestrator (which answers questions). It runs once to *compile* an
existing chart's query logic into a parameterized, re-runnable recipe.
"""

from __future__ import annotations

import json
from typing import Any

_SYSTEM = """\
You compile an existing chart's SQL into a PARAMETERIZED recipe for an interactive
dashboard. You are NOT writing the chart shape (that already exists) and you are
NOT answering a question — you only turn fixed SQL into read-only SELECT templates
with user-controllable :param placeholders, plus the control schema.

Output STRICT JSON only:
{
  "sources": [{"id": "main", "sql": "<read-only SELECT with :param tokens>", "label": ""}],
  "params":  [{"name": "...", "type": "text|number|date|enum", "label": "...",
               "default": <value or dynamic token>, "options": [...], "multi": false}],
  "combine": {"mode": "single|union|join", "key": "", "tag_field": ""}
}

Rules:
- Read-only: every source is a single SELECT. Never write/DDL/DML.
- Declare EVERY :param you use; use only declared names. A name maps 1:1 to a control.
- Keep the SELECT's output columns the same as the original (the chart plan reads
  them by name) — only add WHERE/filter parameters, don't change the projection.
- A placeholder may sit on an EXPRESSION, not just a raw column:
  YEAR(order_date) = :y , lower(name) LIKE :kw , amount > :threshold.
- Param kinds:
  * range  → TWO params, e.g. WHERE d BETWEEN :start AND :end
  * enum multi-select → one param with "multi": true, used as: col IN (:cats)
  * text match → type "text", used as: col LIKE :kw  (value may include %)
  * single value → col = :p
- Dynamic defaults (resolved at run time): "@today", "@yesterday", "@month_start",
  "@year_start", "@quarter_start", "@days_ago:N", "@months_ago:N", "@year",
  "@month", "@month_str". Use these so the board opens on a sensible window.
- combine: "single" for one source; "union" to stack several queries as series
  (set tag_field to the column that holds each source's label); "join" to align
  several queries on a shared key (set key to that column).
- Only parameterize filters a user would actually change. Don't invent params.
"""


def dashboard_compiler_system_prompt() -> str:
    return _SYSTEM


def dashboard_compiler_user_prompt(
    *,
    nl_question: str,
    source_sql: str,
    chart_type: str,
    plan_fields: dict[str, Any],
    schema_context: str = "",
) -> str:
    parts = [
        f"Original question: {nl_question}".strip(),
        f"Chart type: {chart_type}",
        "Chart plan output columns to PRESERVE: " + json.dumps(plan_fields, ensure_ascii=False),
        "Existing SQL:\n" + source_sql.strip(),
    ]
    if schema_context.strip():
        parts.append("Schema (tables/columns you may filter on):\n" + schema_context.strip())
    parts.append("Produce the parameterized recipe JSON.")
    return "\n\n".join(parts)
