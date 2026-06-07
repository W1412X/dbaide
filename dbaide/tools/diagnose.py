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
        return {
            "ok": True,
            "stage": "explain",
            "explain": explain.rows,
        }
