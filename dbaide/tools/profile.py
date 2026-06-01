from __future__ import annotations

from dbaide.adapters.base import DatabaseAdapter
from dbaide.assets import AssetStore
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ColumnProfile, QueryResult


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
