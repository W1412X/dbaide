"""Accumulated SQL execution records for one Ask run."""

from __future__ import annotations

from typing import Any

MAX_SQL_PURPOSE_LEN = 20


def normalize_sql_purpose(text: str) -> str:
    """Short label for why a query ran (model-provided, ≤20 chars)."""
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= MAX_SQL_PURPOSE_LEN:
        return cleaned
    return cleaned[:MAX_SQL_PURPOSE_LEN].rstrip()


def record_sql_execution(
    run_state: Any,
    *,
    sql: str,
    purpose: str,
    database: str,
    tool: str,
    row_count: int,
    elapsed_ms: float,
    artifact_id: str,
    columns: list[str] | None = None,
) -> dict[str, Any]:
    """Append one successful DB execution to the current run."""
    bucket = getattr(run_state, "executed_sqls", None)
    if bucket is None:
        run_state.executed_sqls = []
        bucket = run_state.executed_sqls
    entry: dict[str, Any] = {
        "index": len(bucket) + 1,
        "sql": str(sql or "").strip(),
        "purpose": normalize_sql_purpose(purpose),
        "database": str(database or "").strip(),
        "tool": str(tool or "").strip(),
        "row_count": int(row_count or 0),
        "elapsed_ms": round(float(elapsed_ms or 0.0), 1),
        "artifact_id": str(artifact_id or "").strip(),
    }
    if columns:
        entry["columns"] = [str(c) for c in columns[:12]]
    bucket.append(entry)
    return entry


def response_sql_exports(run_state: Any) -> tuple[str, list[dict[str, Any]]]:
    """Return (selected_sql, executed_sqls) for workflow/session export."""
    executed = [
        dict(item)
        for item in (getattr(run_state, "executed_sqls", None) or [])
        if isinstance(item, dict) and str(item.get("sql") or "").strip()
    ]
    if executed:
        return str(executed[-1]["sql"]), executed
    legacy = str(getattr(run_state, "sql", "") or "").strip()
    return legacy, []
