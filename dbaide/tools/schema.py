from __future__ import annotations

from dbaide.adapters.base import DatabaseAdapter
from dbaide.assets import AssetStore
from dbaide.db.identifiers import normalize_db_table_for_dialect
from dbaide.connection_identity import connection_fingerprint
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import ColumnInfo, ForeignKeyInfo, TableInfo


class SchemaTools:
    def __init__(self, adapter: DatabaseAdapter, context: DisclosureContext, *, instance: str = "", assets: AssetStore | None = None) -> None:
        self.adapter = adapter
        self.context = context
        self.instance = instance or adapter.config.name
        self.assets = assets or AssetStore()
        self.connection = adapter.config
        self.fingerprint = connection_fingerprint(adapter.config)

    def disclose_instance(self) -> None:
        self.context.set_instance(self.instance)

    def list_databases(self) -> list[str]:
        asset_dbs = [
            str(db.get("name"))
            for db in self.assets.database_docs(self.instance, fingerprint=self.fingerprint)
            if db.get("name")
        ]
        if asset_dbs:
            self.context.record_databases(asset_dbs)
            return asset_dbs
        databases = self.adapter.list_databases()
        self.context.record_databases(databases)
        return databases

    def list_tables(self, database: str = "") -> list[TableInfo]:
        database = database or self._default_asset_database()
        if not database:
            all_tables: list[TableInfo] = []
            for db_doc in self.assets.database_docs(self.instance, fingerprint=self.fingerprint):
                db_name = str(db_doc.get("name") or "")
                docs = self.assets.table_docs(self.instance, db_name, fingerprint=self.fingerprint)
                if docs:
                    tables = [self.assets.to_table_info(doc) for doc in docs]
                    self.context.record_tables(tables, database=db_name)
                    all_tables.extend(tables)
            if all_tables:
                return all_tables
        docs = self.assets.table_docs(self.instance, database, fingerprint=self.fingerprint) if database else []
        if docs:
            tables = [self.assets.to_table_info(doc) for doc in docs]
            self.context.record_tables(tables, database=database)
            return tables
        tables = self.adapter.list_tables(database=database)
        self.context.record_tables(tables, database=database)
        return tables

    def describe_table(self, table: str, database: str = "") -> list[ColumnInfo]:
        database, table = normalize_db_table_for_dialect(table, database, self.adapter.dialect)
        database = database or self._asset_database_for_table(table) or self._default_asset_database()
        docs = self.assets.column_docs(self.instance, database, table, fingerprint=self.fingerprint) if database else []
        if docs:
            columns = [self.assets.to_column_info(doc) for doc in docs]
            self._ensure_asset_disclosure_path(database, table)
            self.context.record_columns(table, columns, database=database)
            return columns
        columns = self.adapter.describe_table(table, database=database)
        self.context.record_columns(table, columns, database=database)
        return columns

    def columns_from_assets(self, table: str, database: str = "") -> tuple[str, list[ColumnInfo]] | None:
        """Assets-only column lookup (NO database round-trip). Returns
        (resolved_database, columns) when the offline schema cache has them, else
        None. Used to cheaply rehydrate tables disclosed in earlier turns without
        re-describing against the live DB."""
        database, table = normalize_db_table_for_dialect(table, database, self.adapter.dialect)
        database = database or self._asset_database_for_table(table) or self._default_asset_database()
        if not database:
            return None
        docs = self.assets.column_docs(self.instance, database, table, fingerprint=self.fingerprint)
        if not docs:
            return None
        return database, [self.assets.to_column_info(doc) for doc in docs]

    def foreign_keys(self, table: str, database: str = "") -> list[ForeignKeyInfo]:
        database, table = normalize_db_table_for_dialect(table, database, self.adapter.dialect)
        return self.adapter.foreign_keys(table, database=database)

    def inspect_table(self, table: str, database: str = "") -> dict:
        database, table = normalize_db_table_for_dialect(table, database, self.adapter.dialect)
        columns = self.describe_table(table, database=database)
        fks = self.foreign_keys(table, database=database)
        return {
            "table": table,
            "columns": columns,
            "foreign_keys": fks,
        }

    def _default_asset_database(self) -> str:
        docs = self.assets.database_docs(self.instance, fingerprint=self.fingerprint)
        if len(docs) == 1:
            return str(docs[0].get("name") or "")
        return ""

    def _asset_database_for_table(self, table: str) -> str:
        matches: list[str] = []
        for db_doc in self.assets.database_docs(self.instance, fingerprint=self.fingerprint):
            db_name = str(db_doc.get("name") or "")
            for table_doc in self.assets.table_docs(self.instance, db_name, fingerprint=self.fingerprint):
                if table_doc.get("name") == table or table_doc.get("table") == table:
                    matches.append(db_name)
                    break
        return matches[0] if len(matches) == 1 else ""

    def _ensure_asset_disclosure_path(self, database: str, table: str) -> None:
        """Asset-backed describe_table can jump straight to columns; record the
        containing database/table first so disclosure traces stay monotonic."""
        if not database:
            return
        if database not in self.context.databases:
            dbs = [
                str(db.get("name"))
                for db in self.assets.database_docs(self.instance, fingerprint=self.fingerprint)
                if db.get("name")
            ]
            self.context.record_databases(dbs or [database])
        ref = self.context.table_ref(database, table)
        if ref not in self.context.tables:
            table_doc = self.assets.table_doc(
                self.instance,
                database,
                table,
                fingerprint=self.fingerprint,
            ) or {"name": table, "database": database}
            self.context.record_tables(
                [self.assets.to_table_info(table_doc)],
                database=database,
            )
