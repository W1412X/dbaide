"""Resolve tabular rows for chart rendering from tool args / run state."""

from __future__ import annotations

from typing import Any


def resolve_chart_rows(
    orchestrator: Any,
    *,
    artifact_id: str = "",
    data: list[dict[str, Any]] | None = None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (rows, columns) for charting, capped at ``limit`` rows."""
    if data:
        rows = [_row_dict(r) for r in data if isinstance(r, dict)]
        if rows:
            return rows[:limit], _columns_from_rows(rows)

    memory = orchestrator.run_state.memory
    art_id = str(artifact_id or "").strip()
    if art_id:
        artifact = _find_sql_artifact(memory, art_id)
        if artifact is not None:
            qr = orchestrator.run_state.query_result
            if qr and qr.rows and _artifact_matches_query(artifact, qr):
                rows = [_row_dict(r) for r in qr.rows if isinstance(r, dict)]
            else:
                rows = [_row_dict(r) for r in (artifact.rows_preview or []) if isinstance(r, dict)]
            if rows:
                return rows[:limit], list(artifact.columns or _columns_from_rows(rows))

    qr = orchestrator.run_state.query_result
    if qr and qr.rows:
        rows = [_row_dict(r) for r in qr.rows if isinstance(r, dict)]
        if rows:
            return rows[:limit], list(qr.columns or _columns_from_rows(rows))

    return [], []


def _row_dict(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    return list(rows[0].keys())


def _find_sql_artifact(memory: Any, artifact_id: str) -> Any | None:
    for art in reversed(getattr(memory, "sql_artifacts", []) or []):
        if str(getattr(art, "id", "") or "") == artifact_id:
            return art
    return None


def _artifact_matches_query(artifact: Any, qr: Any) -> bool:
    art_sql = str(getattr(artifact, "sql", "") or "").strip()
    qr_sql = str(getattr(qr, "sql", "") or "").strip()
    if art_sql and qr_sql:
        return art_sql == qr_sql
    return True
