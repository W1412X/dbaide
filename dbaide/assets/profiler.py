from __future__ import annotations

import logging
from typing import Any

from dbaide.adapters.base import DatabaseAdapter, quote_identifier
from dbaide.models import ColumnInfo, ColumnProfile

logger = logging.getLogger("dbaide.profiler")


class ColumnProfiler:
    """
    Builds rich, type-aware column profiles for offline assets.

    The profiler intentionally uses simple portable SQL plus bounded sampling.
    It does not use embeddings and does not scan more detail than needed for the
    asset document.
    """

    def __init__(self, adapter: DatabaseAdapter, *, timeout_seconds: int = 30) -> None:
        self.adapter = adapter
        self.timeout_seconds = timeout_seconds

    def profile(self, table: str, column: ColumnInfo, *, database: str = "", top_k: int = 20,
                sample_limit: int = 50, timeout_seconds: int | None = None) -> ColumnProfile:
        timeout = timeout_seconds or self.timeout_seconds
        base = self.adapter.profile_column(table, column.name, database=database, top_k=top_k,
                                           timeout_seconds=timeout)
        kind = infer_data_kind(column, base)
        row_count = max(0, int(base.row_count or 0))
        null_count = max(0, int(base.null_count or 0))
        distinct_count = base.distinct_count
        base.data_kind = kind
        base.null_rate = (null_count / row_count) if row_count else None
        base.distinct_ratio = (distinct_count / row_count) if row_count and distinct_count is not None else None
        base.distribution = build_distribution_summary(base, top_k=top_k)
        base.sample_rows = self._sample_context_rows(table, column.name, database=database,
                                                     limit=min(sample_limit, 50), timeout_seconds=timeout)
        if kind == "numeric":
            base.numeric_stats = self._numeric_stats(table, column.name, database=database, timeout_seconds=timeout)
        elif kind == "text":
            base.text_stats = self._text_stats(table, column.name, database=database, timeout_seconds=timeout)
        elif kind == "temporal":
            base.temporal_stats = {
                "min": base.min_value,
                "max": base.max_value,
                "sample_values": base.sample_values[:sample_limit],
            }
        elif kind in {"categorical", "boolean"}:
            pass
        return base

    def _numeric_stats(self, table: str, column: str, *, database: str,
                       timeout_seconds: int = 10) -> dict[str, Any]:
        tq, cq = quote_identifier(table, self.adapter.dialect), quote_identifier(column, self.adapter.dialect)
        try:
            result = self.adapter.execute_readonly(
                f"SELECT AVG({cq}) AS avg_value FROM {tq} WHERE {cq} IS NOT NULL",
                database=database, limit=None, timeout_seconds=timeout_seconds,
            )
            avg = result.rows[0].get("avg_value") if result.rows else None
        except Exception:
            avg = None
        return {"avg": avg}

    def _text_stats(self, table: str, column: str, *, database: str,
                    timeout_seconds: int = 10) -> dict[str, Any]:
        tq, cq = quote_identifier(table, self.adapter.dialect), quote_identifier(column, self.adapter.dialect)
        length_expr = f"LENGTH({cq})"
        if self.adapter.dialect == "postgres":
            length_expr = f"LENGTH({cq}::text)"
        try:
            result = self.adapter.execute_readonly(
                f"SELECT MIN({length_expr}) AS min_length, MAX({length_expr}) AS max_length, "
                f"AVG({length_expr}) AS avg_length FROM {tq} WHERE {cq} IS NOT NULL",
                database=database, limit=None, timeout_seconds=timeout_seconds,
            )
            return dict(result.rows[0]) if result.rows else {}
        except Exception:
            return {}

    def _sample_context_rows(self, table: str, column: str, *, database: str, limit: int,
                             timeout_seconds: int = 10) -> list[dict[str, Any]]:
        tq, cq = quote_identifier(table, self.adapter.dialect), quote_identifier(column, self.adapter.dialect)
        order_expr = "RANDOM()"
        if self.adapter.dialect == "mysql":
            order_expr = "RAND()"
        try:
            result = self.adapter.execute_readonly(
                f"SELECT {cq} AS value FROM {tq} WHERE {cq} IS NOT NULL ORDER BY {order_expr}",
                database=database, limit=limit, timeout_seconds=timeout_seconds,
            )
            return result.rows
        except Exception:
            return []


def infer_data_kind(column: ColumnInfo, profile: ColumnProfile | None = None) -> str:
    name = column.name.lower()
    typ = (column.data_type or "").lower()
    if any(k in typ for k in ["bool", "bit"]) or name.startswith("is_") or name.startswith("has_"):
        return "boolean"
    time_name = (
        name in {"date", "day", "time", "timestamp"}
        or name.endswith("_at")
        or name.endswith("_time")
        or name.endswith("_date")
        or "created" in name
        or "updated" in name
    )
    if time_name or any(k in typ for k in ["date", "time", "timestamp"]):
        return "temporal"
    if any(k in name for k in ["status", "state", "type", "category", "kind", "level"]):
        return "categorical"
    if any(k in typ for k in ["int", "real", "numeric", "decimal", "float", "double", "number"]):
        if profile and profile.distinct_count is not None and profile.row_count and profile.distinct_count <= min(50, max(3, profile.row_count // 20)):
            return "categorical"
        return "numeric"
    if profile and profile.distinct_count is not None and profile.row_count and profile.distinct_count <= min(50, max(3, profile.row_count // 20)):
        return "categorical"
    if any(k in typ for k in ["char", "text", "json", "uuid"]):
        return "text"
    return "unknown"


def build_distribution_summary(profile: ColumnProfile, *, top_k: int) -> dict[str, Any]:
    non_null = max(0, int(profile.row_count or 0) - int(profile.null_count or 0))
    return {
        "non_null_count": non_null,
        "top_k": top_k,
        "top_values": profile.top_values[:top_k],
        "sample_values": profile.sample_values[:top_k],
        "top_values_coverage": _coverage(profile.top_values[:top_k], non_null),
        "truncated_distinct_values": bool(profile.distinct_count is not None and profile.distinct_count > len(profile.top_values)),
    }


def _coverage(values: list[dict[str, Any]], denominator: int) -> float | None:
    if denominator <= 0:
        return None
    total = 0
    for item in values:
        try:
            total += int(item.get("count") or 0)
        except Exception:
            continue
    return total / denominator
