"""Single-call LLM SQL optimization advisor.

Given a query, its EXPLAIN plan, and the relevant table schema (columns, indexes,
foreign keys), the optimizer makes ONE model call and returns concrete optimization
*suggestions* as text. It is advisory only — it never rewrites the SQL, never executes,
and never blocks. The main agent reads the suggestions and decides whether to issue a
better query; the Workbench shows them in a panel.

Reuses the LLM client / message types from :mod:`dbaide.llm` (same plumbing as the
chart agent). With a ``NullLLMClient`` (no model configured) it simply returns ``None``.
"""

from __future__ import annotations

from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.models import QueryResult

_MAX_TABLES = 8
_MAX_PLAN_ROWS = 60


def format_explain(explain: QueryResult) -> str:
    """Render an EXPLAIN result (tabular MySQL/SQLite or text Postgres) as plain lines."""
    cols = [str(c) for c in (explain.columns or [])]
    lines: list[str] = []
    if len(cols) > 1:
        lines.append(" | ".join(cols))
    for row in (explain.rows or [])[:_MAX_PLAN_ROWS]:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    return "\n".join(lines).strip()


def build_schema_digest(adapter, tables: list[str], *, database: str = "") -> str:
    """A compact columns + indexes + FKs digest for the tables a query touches."""
    blocks: list[str] = []
    for table in tables[:_MAX_TABLES]:
        try:
            cols = adapter.describe_table(table, database=database)
        except Exception:  # noqa: BLE001 - best-effort schema; never fail the advisor
            cols = []
        try:
            idxs = adapter.indexes(table, database=database)
        except Exception:  # noqa: BLE001
            idxs = []
        try:
            fks = adapter.foreign_keys(table, database=database)
        except Exception:  # noqa: BLE001
            fks = []
        if not cols and not idxs:
            continue
        col_str = ", ".join(
            (f"{c.name} {c.data_type}".strip() + ("*" if getattr(c, "primary_key", False) else ""))
            for c in cols[:60]
        )
        lines = [f"TABLE {table}: {col_str}"]
        if idxs:
            lines.append("  indexes: " + "; ".join(
                f"{i.name}({', '.join(i.columns)})" + (" UNIQUE" if getattr(i, 'unique', False) else "")
                for i in idxs[:20]))
        if fks:
            lines.append("  foreign keys: " + "; ".join(
                f"{f.column} -> {f.ref_table}.{f.ref_column}" for f in fks[:20]))
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


_SYSTEM_PROMPT = (
    "You are a SQL performance optimization advisor for a read-only analytics assistant. "
    "You are given a SQL query, its EXPLAIN plan, and the relevant table schema (columns, "
    "indexes, foreign keys). Explain briefly why the query is expensive and give concrete, "
    "specific optimization suggestions that reference the ACTUAL tables/columns/indexes. "
    "Consider, when relevant: full/sequential scans and which index would help; non-sargable "
    "predicates (a function wrapping a filtered column, leading-wildcard LIKE, implicit type "
    "casts); SELECT * vs. only the needed columns; accidental cross joins / missing join "
    "conditions; pushing filters and aggregation down; OR vs. UNION; redundant DISTINCT or "
    "ORDER BY. Do NOT rewrite the query and do NOT restate it — give a short bulleted list of "
    "suggestions only (no preamble). If the query already looks efficient, say so in one line."
)


def _user_prompt(sql: str, explain_text: str, schema_text: str, dialect: str) -> str:
    parts = [f"Dialect: {dialect or 'generic'}", "", "SQL:", sql.strip()]
    if explain_text:
        parts += ["", "EXPLAIN plan:", explain_text]
    if schema_text:
        parts += ["", "Relevant schema:", schema_text]
    parts += ["", "Optimization suggestions:"]
    return "\n".join(parts)


class OptimizerAgent:
    """One model call: (SQL, EXPLAIN, schema) -> optimization suggestions text."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or NullLLMClient()

    def evaluate(self, sql: str, *, explain_text: str = "", schema_text: str = "",
                 dialect: str = "") -> str | None:
        """Return suggestions text, or None (no model, empty SQL, or model error)."""
        if not sql.strip() or isinstance(self.llm, NullLLMClient):
            return None
        try:
            text = self.llm.complete_text([
                LLMMessage("system", _SYSTEM_PROMPT),
                LLMMessage("user", _user_prompt(sql, explain_text, schema_text, dialect)),
            ])
        except Exception:  # noqa: BLE001 - advisory: a model hiccup must not fail the query
            return None
        text = (text or "").strip()
        return text or None

    def evaluate_sql(self, sql: str, *, query_tools, database: str = "") -> str | None:
        """Convenience: build the EXPLAIN + schema context from a QueryTools, then evaluate.

        ``query_tools`` only needs ``.explain_sql`` and ``.adapter`` (describe/indexes/FKs)."""
        explain_text = ""
        try:
            explain_text = format_explain(query_tools.explain_sql(sql, database=database))
        except Exception:  # noqa: BLE001
            explain_text = ""
        from dbaide.agent.toolkit.support import _tables_in_sql  # lazy: avoid import cycle
        try:
            tables = _tables_in_sql(sql)
        except Exception:  # noqa: BLE001
            tables = []
        schema_text = build_schema_digest(query_tools.adapter, tables, database=database)
        dialect = getattr(query_tools.adapter, "dialect", "")
        return self.evaluate(sql, explain_text=explain_text, schema_text=schema_text, dialect=dialect)
