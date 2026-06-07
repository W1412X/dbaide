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
                sample_limit: int = 50, timeout_seconds: int | None = None,
                heavy_scan: bool = True) -> ColumnProfile:
        timeout = timeout_seconds or self.timeout_seconds
        # Pre-classify by declared type so the adapter can fold avg/length into the
        # single aggregate scan instead of issuing extra full-table queries.
        type_kind = kind_from_type(column)
        base = self.adapter.profile_column(
            table, column.name, database=database, top_k=top_k, timeout_seconds=timeout,
            heavy_scan=heavy_scan,
            include_avg=(type_kind == "numeric"),
            include_length=(type_kind == "text"),
        )
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
        # numeric_stats / text_stats are populated by the adapter's merged scan.
        if kind == "temporal":
            base.temporal_stats = {
                "min": base.min_value,
                "max": base.max_value,
                "sample_values": base.sample_values[:sample_limit],
            }
        return base

    def _sample_context_rows(self, table: str, column: str, *, database: str, limit: int,
                             timeout_seconds: int = 10) -> list[dict[str, Any]]:
        # No ORDER BY RAND()/RANDOM(): that forces a full-table sort and is a known
        # performance killer. A bare LIMIT lets the engine stop early — negligible cost.
        tq, cq = quote_identifier(table, self.adapter.dialect), quote_identifier(column, self.adapter.dialect)
        try:
            result = self.adapter.execute_readonly(
                f"SELECT {cq} AS value FROM {tq} WHERE {cq} IS NOT NULL",
                database=database, limit=limit, timeout_seconds=timeout_seconds,
            )
            return result.rows
        except Exception:
            return []


def kind_from_type(column: ColumnInfo) -> str:
    """Classify a column from its declared type only (no profile needed).

    Used up front so the adapter can decide which aggregates to fold into its
    single scan. The richer :func:`infer_data_kind` refines this afterwards.
    """
    typ = _base_type(column.data_type)
    if typ in {"bool", "boolean", "bit"}:
        return "boolean"
    if typ in {"date", "time", "timestamp", "timestamptz", "datetime"}:
        return "temporal"
    if typ in {"int", "integer", "bigint", "smallint", "tinyint", "serial", "real", "numeric", "decimal", "float", "double", "number"}:
        return "numeric"
    if typ in {"char", "varchar", "nchar", "nvarchar", "text", "json", "jsonb", "uuid", "blob", "clob"}:
        return "text"
    return "unknown"


def infer_data_kind(column: ColumnInfo, profile: ColumnProfile | None = None) -> str:
    typ = _base_type(column.data_type)
    if typ in {"bool", "boolean", "bit"}:
        return "boolean"
    if typ in {"date", "time", "timestamp", "timestamptz", "datetime"}:
        return "temporal"
    if typ in {"int", "integer", "bigint", "smallint", "tinyint", "serial", "real", "numeric", "decimal", "float", "double", "number"}:
        if (
            profile
            and profile.distinct_count is not None
            and profile.row_count
            and profile.distinct_count <= min(50, max(3, profile.row_count // 20))
        ):
            return "categorical"
        return "numeric"
    if (
        profile
        and profile.distinct_count is not None
        and profile.row_count
        and profile.distinct_count <= min(50, max(3, profile.row_count // 20))
    ):
        return "categorical"
    if typ in {"char", "varchar", "nchar", "nvarchar", "text", "json", "jsonb", "uuid", "blob", "clob"}:
        return "text"
    return "unknown"


def _base_type(data_type: str | None) -> str:
    text = str(data_type or "").strip().lower()
    if not text:
        return ""
    return text.split("(", 1)[0].split()[0]


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
