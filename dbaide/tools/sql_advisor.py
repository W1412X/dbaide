"""Advisory SQL optimizer.

For a query whose EXPLAIN cost exceeds ``optimize_advise_rows``, surface concrete,
human-readable optimization *suggestions* to the main agent — it never rewrites the SQL
or blocks execution. The agent reads the advice and decides whether to issue a better
query. Signals come from the EXPLAIN plan (full scans / no index used) plus cheap regex
lints (SELECT *, leading-wildcard LIKE, a function wrapped around a filtered column).

Everything is best-effort: a plan we can't parse or an EXPLAIN that fails just yields
fewer findings, never an error.
"""

from __future__ import annotations

import re

from dbaide.models import QueryResult

_SELECT_STAR = re.compile(r"\bselect\s+(?:[a-z_]\w*\.)?\*", re.IGNORECASE)
_LEADING_WILDCARD = re.compile(r"\blike\s+'\s*%", re.IGNORECASE)
_FUNC_ON_COL = re.compile(
    r"\b(?:lower|upper|date|year|month|day|cast|coalesce|substr|substring|trim|concat)"
    r"\s*\([^()]*\b[a-z_]\w*\b[^()]*\)\s*(?:=|<>|!=|<=|>=|<|>|\blike\b|\bin\b)",
    re.IGNORECASE,
)


def _plan_findings(dialect: str, explain: QueryResult) -> list[str]:
    """Best-effort: pull full-scan / no-index signals out of an EXPLAIN result."""
    out: list[str] = []
    d = (dialect or "").lower()
    cols = [str(c).lower() for c in (explain.columns or [])]
    rows = explain.rows or []
    try:
        if "sqlite" in d:
            for r in rows:
                cell = " ".join(str(x) for x in r).upper()
                if "SCAN" in cell and "USING INDEX" not in cell and "USING COVERING INDEX" not in cell:
                    m = re.search(r"SCAN (?:TABLE )?(\w+)", cell)
                    out.append(f"full table scan on {m.group(1).lower() if m else 'a table'} (no usable index)")
        elif "mysql" in d or "maria" in d:
            ti = cols.index("type") if "type" in cols else -1
            ki = cols.index("key") if "key" in cols else -1
            tbi = cols.index("table") if "table" in cols else -1
            for r in rows:
                typ = str(r[ti]).upper() if 0 <= ti < len(r) else ""
                key = r[ki] if 0 <= ki < len(r) else None
                tbl = str(r[tbi]) if 0 <= tbi < len(r) else "a table"
                if typ == "ALL":
                    out.append(f"full table scan on {tbl} (access type ALL — no index used)")
                elif key in (None, "", "NULL"):
                    out.append(f"no index used on {tbl}")
        elif "postgres" in d or "psql" in d or "redshift" in d:
            for r in rows:
                line = " ".join(str(x) for x in r)
                m = re.search(r"Seq Scan on (\w+)", line)
                if m:
                    out.append(f"sequential (full) scan on {m.group(1)}")
    except Exception:  # noqa: BLE001 - advisory: any parse hiccup just drops plan findings
        return out
    seen: set[str] = set()
    uniq: list[str] = []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq[:4]


class SqlAdvisor:
    """Produces optimization advice for an expensive query. Advisory only."""

    def __init__(self, query_tools) -> None:
        self._q = query_tools

    def advise(self, sql: str, *, database: str = "", estimated_rows: int | None = None) -> str | None:
        findings: list[str] = []
        # plan-based signals (best-effort; a second cheap EXPLAIN only for the rare
        # over-threshold query)
        try:
            explain = self._q.explain_sql(sql, database=database)
            findings.extend(_plan_findings(getattr(self._q.adapter, "dialect", ""), explain))
        except Exception:  # noqa: BLE001
            pass
        # cheap regex lints
        if _SELECT_STAR.search(sql):
            findings.append("selects all columns (SELECT *) — project only the columns you need")
        if _LEADING_WILDCARD.search(sql):
            findings.append("a LIKE pattern starts with '%' (leading wildcard) — it can't use an index; "
                            "anchor the prefix or use a full-text search")
        if _FUNC_ON_COL.search(sql):
            findings.append("a function wraps a filtered column (non-sargable) — compare the bare column, "
                            "or filter on a precomputed/derived column that is indexed")
        if not findings:
            return None
        head = (f"This query is heavy (EXPLAIN estimates ~{estimated_rows:,} scanned rows). "
                if estimated_rows is not None else "This query looks heavy. ")
        body = "; ".join(f"({i + 1}) {f}" for i, f in enumerate(findings))
        return (head + "Optimization suggestions: " + body + ". "
                "Consider adding selective filters on indexed columns, narrowing the date/key range, "
                "or aggregating server-side before returning rows.")
