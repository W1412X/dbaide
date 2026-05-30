from __future__ import annotations

from dbaide.adapters.base import DatabaseAdapter
from dbaide.assets import AssetStore
from dbaide.context.catalog import CatalogMatcher, ScoredTable
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ColumnInfo, ForeignKeyInfo, TableInfo


class SchemaTools:
    def __init__(self, adapter: DatabaseAdapter, context: DisclosureContext, *, instance: str = "", assets: AssetStore | None = None) -> None:
        self.adapter = adapter
        self.context = context
        self.instance = instance or adapter.config.name
        self.matcher = CatalogMatcher()
        self.assets = assets or AssetStore()

    def disclose_instance(self) -> None:
        self.context.record_instances([self.instance])

    def list_databases(self) -> list[str]:
        asset_dbs = [str(db.get("name")) for db in self.assets.database_docs(self.instance) if db.get("name")]
        if asset_dbs:
            self.context.record_databases(self.instance, asset_dbs)
            return asset_dbs
        databases = self.adapter.list_databases()
        self.context.record_databases(self.instance, databases)
        return databases

    def list_tables(self, database: str = "") -> list[TableInfo]:
        database = database or self._default_asset_database()
        if not database:
            all_tables: list[TableInfo] = []
            for db_doc in self.assets.database_docs(self.instance):
                db_name = str(db_doc.get("name") or "")
                docs = self.assets.table_docs(self.instance, db_name)
                if docs:
                    tables = [self.assets.to_table_info(doc) for doc in docs]
                    self.context.record_tables(tables, instance=self.instance, database=db_name)
                    all_tables.extend(tables)
            if all_tables:
                return all_tables
        docs = self.assets.table_docs(self.instance, database) if database else []
        if docs:
            tables = [self.assets.to_table_info(doc) for doc in docs]
            self.context.record_tables(tables, instance=self.instance, database=database)
            return tables
        tables = self.adapter.list_tables(database=database)
        self.context.record_tables(tables, instance=self.instance, database=database)
        return tables

    def candidate_tables(self, query: str, *, database: str = "", limit: int = 8) -> list[ScoredTable]:
        tables = self.list_tables(database=database)
        return self.matcher.score_tables(query, tables, limit=limit)

    def describe_table(self, table: str, database: str = "") -> list[ColumnInfo]:
        database = database or self._asset_database_for_table(table) or self._default_asset_database()
        docs = self.assets.column_docs(self.instance, database, table) if database else []
        if docs:
            columns = [self.assets.to_column_info(doc) for doc in docs]
            self.context.record_columns(table, columns, instance=self.instance, database=database)
            return columns
        columns = self.adapter.describe_table(table, database=database)
        self.context.record_columns(table, columns, instance=self.instance, database=database)
        return columns

    def foreign_keys(self, table: str, database: str = "") -> list[ForeignKeyInfo]:
        return self.adapter.foreign_keys(table, database=database)

    def inspect_table(self, table: str, database: str = "") -> dict:
        columns = self.describe_table(table, database=database)
        fks = self.foreign_keys(table, database=database)
        return {
            "table": table,
            "columns": columns,
            "foreign_keys": fks,
        }

    def _default_asset_database(self) -> str:
        docs = self.assets.database_docs(self.instance)
        if len(docs) == 1:
            return str(docs[0].get("name") or "")
        return ""

    def _asset_database_for_table(self, table: str) -> str:
        for db_doc in self.assets.database_docs(self.instance):
            db_name = str(db_doc.get("name") or "")
            for table_doc in self.assets.table_docs(self.instance, db_name):
                if table_doc.get("name") == table or table_doc.get("table") == table:
                    return db_name
        return ""
