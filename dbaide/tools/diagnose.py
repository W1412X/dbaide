from __future__ import annotations

from dbaide.tools.query import QueryTools


class DiagnoseTools:
    def __init__(self, query_tools: QueryTools) -> None:
        self.query_tools = query_tools

    def diagnose_sql(self, sql: str, *, database: str = "") -> dict:
        validation = self.query_tools.validate_sql(sql, add_limit=False)
        if not validation.ok:
            return {
                "ok": False,
                "stage": "validation",
                "issues": [issue.message for issue in validation.issues],
            }
        try:
            explain = self.query_tools.explain_sql(validation.normalized_sql, database=database)
        except Exception as exc:
            return {"ok": False, "stage": "explain", "issues": [str(exc)]}
        hints = []
        text = " ".join(str(row) for row in explain.rows).lower()
        if "scan" in text or "seq scan" in text or "all" in text:
            hints.append("EXPLAIN shows possible full table scan; check if filter/join columns have indexes.")
        if "temporary" in text or "filesort" in text:
            hints.append("EXPLAIN shows temporary table or filesort; ORDER BY/GROUP BY columns may need indexes.")
        if not hints:
            hints.append("No obvious performance risks found. Still recommend reviewing with data volume and business context.")
        return {
            "ok": True,
            "stage": "explain",
            "explain": explain.rows,
            "hints": hints,
        }
