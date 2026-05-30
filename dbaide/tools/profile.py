from __future__ import annotations

import dataclasses

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
                profile_data = doc.get("profile") or {}
                if profile_data:
                    valid_keys = {f.name for f in dataclasses.fields(ColumnProfile)}
                    filtered = {k: v for k, v in profile_data.items() if k in valid_keys}
                    profile = ColumnProfile(**filtered)
                    self.context.record_profile(table, profile, instance=self.instance, database=database)
                    return profile
        profile = self.adapter.profile_column(table, column, database=database, top_k=top_k)
        self.context.record_profile(table, profile, instance=self.instance, database=database)
        return profile

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
