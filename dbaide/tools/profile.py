from __future__ import annotations

from typing import Any

from dbaide.adapters.base import DatabaseAdapter, quote_identifier
from dbaide.assets import AssetStore
from dbaide.assets.profiler import kind_from_type
from dbaide.assets.summarizer import truncate_cell
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ColumnInfo, ColumnProfile, QueryResult

# Type-aware candidate metrics. The first list is the default set (always fetched
# when the caller doesn't pick); the second is optional metrics the LLM opts into.
_METRICS: dict[str, tuple[list[str], list[str]]] = {
    "numeric": (["min", "max", "null_rate"], ["distinct_count"]),
    "temporal": (["min", "max", "null_rate"], ["distinct_count"]),
    "text": (["min_len", "max_len", "null_rate", "empty_rate"], ["distinct_count"]),
    "boolean": (["distinct_count", "null_rate"], []),
    "categorical": (["distinct_count", "null_rate"], ["top_values"]),
    "unknown": (["null_rate"], ["distinct_count"]),
}

# SQL aggregate expression per metric (portable across sqlite/mysql/postgres).
_AGG = {
    "min": "MIN({c})",
    "max": "MAX({c})",
    "null_rate": "AVG(CASE WHEN {c} IS NULL THEN 1.0 ELSE 0.0 END)",
    "distinct_count": "COUNT(DISTINCT {c})",
    "min_len": "MIN(LENGTH({c}))",
    "max_len": "MAX(LENGTH({c}))",
    "empty_rate": "AVG(CASE WHEN {c} = '' THEN 1.0 ELSE 0.0 END)",
}


class ProfileTools:
    def __init__(self, adapter: DatabaseAdapter, context: DisclosureContext, *, instance: str = "", assets: AssetStore | None = None) -> None:
        self.adapter = adapter
        self.context = context
        self.instance = instance or adapter.config.name
        self.assets = assets or AssetStore()

    def sample_rows(self, table: str, *, database: str = "", limit: int = 20) -> QueryResult:
        result = self.adapter.sample_rows(table, database=database, limit=limit)
        self.context.record_samples(table, result.rows, instance=self.instance, database=database)
        return result

    def profile_column(self, table: str, column: str, *, database: str = "", top_k: int = 10) -> ColumnProfile:
        database = database or self._asset_database_for_table(table) or self._default_asset_database()
        for doc in self.assets.column_docs(self.instance, database, table) if database else []:
            if doc.get("name") == column or doc.get("column") == column:
                cached = self._profile_from_doc(table, column, doc)
                if cached is not None:
                    self.context.record_profile(table, cached, instance=self.instance, database=database)
                    return cached
        profile = self.adapter.profile_column(table, column, database=database, top_k=top_k)
        self.context.record_profile(table, profile, instance=self.instance, database=database)
        return profile

    @staticmethod
    def _profile_from_doc(table: str, column: str, doc: dict) -> ColumnProfile | None:
        """Reconstruct a ColumnProfile from a stored asset column document so a
        fresh offline profile avoids a live DB scan. Returns None when the column
        was not profiled. The persisted stats live under the ``statistics`` key
        (with top/sample values at the top level), not ``profile``."""
        if doc.get("profile_status") != "profiled":
            return None
        stats = doc.get("statistics") or {}
        if not stats:
            return None
        return ColumnProfile(
            table=table,
            column=column,
            row_count=int(stats.get("row_count") or 0),
            null_count=int(stats.get("null_count") or 0),
            distinct_count=stats.get("distinct_count"),
            min_value=stats.get("min_value"),
            max_value=stats.get("max_value"),
            top_values=doc.get("top_values") or [],
            sample_values=doc.get("sample_values") or [],
            data_kind=stats.get("data_kind") or "unknown",
            null_rate=stats.get("null_rate"),
            distinct_ratio=stats.get("distinct_ratio"),
            numeric_stats=stats.get("numeric_stats") or {},
            text_stats=stats.get("text_stats") or {},
            temporal_stats=stats.get("temporal_stats") or {},
            distribution=stats.get("distribution") or {},
            sample_rows=stats.get("sample_rows") or [],
        )

    def column_stats(self, table: str, columns: list[str] | None = None, *,
                     metrics: list[str] | None = None, database: str = "",
                     top_k: int = 10) -> list[dict[str, Any]]:
        """On-demand, type-aware statistics. One bounded aggregate scan per column;
        the caller (LLM) chooses metrics, else type defaults apply. Values truncated."""
        database = database or self._asset_database_for_table(table) or self._default_asset_database()
        all_cols = {c.name: c for c in self.adapter.describe_table(table, database=database)}
        wanted = [all_cols[c] for c in (columns or list(all_cols)) if c in all_cols]
        picked = [str(m).strip().lower() for m in (metrics or []) if str(m).strip()]
        out: list[dict[str, Any]] = []
        for col in wanted:
            kind = kind_from_type(col)
            defaults, optional = _METRICS.get(kind, _METRICS["unknown"])
            chosen = [m for m in picked if m in defaults or m in optional] if picked else list(defaults)
            stats = self._compute_stats(table, col, chosen, database=database, top_k=top_k)
            out.append({"column": col.name, "data_type": col.data_type, "kind": kind, "stats": stats})
        return out

    def _compute_stats(self, table: str, col: ColumnInfo, metrics: list[str], *,
                       database: str, top_k: int) -> dict[str, Any]:
        cq = quote_identifier(col.name, self.adapter.dialect)
        tq = quote_identifier(table, self.adapter.dialect)
        selects, names = [], []
        for m in metrics:
            if m in _AGG:
                selects.append(f"{_AGG[m].format(c=cq)} AS m{len(names)}")
                names.append(m)
        stats: dict[str, Any] = {}
        if selects:
            try:
                res = self.adapter.execute_readonly(
                    f"SELECT {', '.join(selects)} FROM {tq}", database=database, limit=1,
                )
                row = res.rows[0] if res.rows else {}
                vals = list(row.values())
                for i, name in enumerate(names):
                    v = vals[i] if i < len(vals) else None
                    stats[name] = round(float(v), 4) if name.endswith("_rate") and v is not None else truncate_cell(v)
            except Exception as exc:  # surface as a note rather than failing the tool
                stats["error"] = str(exc)
        if "top_values" in metrics:
            try:
                res = self.adapter.execute_readonly(
                    f"SELECT {cq} AS value, COUNT(*) AS n FROM {tq} WHERE {cq} IS NOT NULL "
                    f"GROUP BY {cq} ORDER BY n DESC", database=database, limit=top_k,
                )
                stats["top_values"] = [{"value": truncate_cell(r.get("value")), "count": r.get("n")} for r in res.rows]
            except Exception:
                pass
        return stats

    def profile_table(self, table: str, columns: list[str] | None = None, *, database: str = "", top_k: int = 10) -> list[ColumnProfile]:
        if columns is None:
            columns = [c.name for c in self.adapter.describe_table(table, database=database)]
        profiles: list[ColumnProfile] = []
        for column in columns:
            try:
                profiles.append(self.profile_column(table, column, database=database, top_k=top_k))
            except Exception:
                continue
        return profiles

    def _default_asset_database(self) -> str:
        docs = self.assets.database_docs(self.instance)
        if len(docs) == 1:
            return str(docs[0].get("name") or "")
        return ""

    def _asset_database_for_table(self, table: str) -> str:
        for db_doc in self.assets.database_docs(self.instance):
            db_name = str(db_doc.get("name") or "")
            if any((doc.get("name") == table or doc.get("table") == table) for doc in self.assets.table_docs(self.instance, db_name)):
                return db_name
        return ""
