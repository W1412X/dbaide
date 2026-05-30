from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Callable

from dbaide.adapters.base import DatabaseAdapter
from dbaide.assets.profiler import ColumnProfiler
from dbaide.assets.store import AssetStore
from dbaide.assets.summarizer import AssetSummarizer
from dbaide.llm import LLMClient
from dbaide.models import ConnectionConfig

logger = logging.getLogger("dbaide.builder")


@dataclass(slots=True)
class BuildStats:
    instances: int = 0
    databases: int = 0
    tables: int = 0
    columns: int = 0
    profiled_columns: int = 0
    skipped_profiles: int = 0
    timed_out_columns: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


@dataclass(slots=True)
class BuildOptions:
    sample: bool = True
    profile_mode: str = "auto"
    top_k: int = 30
    sample_limit: int = 50
    per_column_timeout: int = 30
    deadline: float = 0.0
    max_workers: int = 4


class AssetBuilder:
    def __init__(
        self,
        *,
        connection: ConnectionConfig,
        adapter: DatabaseAdapter,
        store: AssetStore | None = None,
        llm: LLMClient | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.adapter = adapter
        self.store = store or AssetStore()
        self.summarizer = AssetSummarizer(llm)
        self.progress = progress or (lambda _msg: None)

    def build(
        self,
        *,
        databases: list[str] | None = None,
        sample: bool = True,
        profile: bool | None = None,
        profile_mode: str = "auto",
        top_k: int = 30,
        sample_limit: int = 50,
        timeout: int = 0,
        per_column_timeout: int = 30,
        max_workers: int = 4,
    ) -> BuildStats:
        started = time.time()
        deadline = started + timeout if timeout > 0 else 0.0
        if profile is not None:
            profile_mode = "all" if profile else "none"
        options = BuildOptions(
            sample=sample,
            profile_mode=normalize_profile_mode(profile_mode),
            top_k=top_k,
            sample_limit=sample_limit,
            per_column_timeout=per_column_timeout,
            deadline=deadline,
            max_workers=max_workers,
        )
        profiler = ColumnProfiler(self.adapter, timeout_seconds=per_column_timeout)
        stats = BuildStats(instances=1)
        instance = self.connection.name

        # Step 1: Test connection
        self.progress(f"[assets] testing connection {instance}")
        self.adapter.test()

        # Step 2: Discover databases
        db_names = self._resolve_databases(databases)
        self.progress(f"[assets] instance={instance}, discovered {len(db_names)} database(s): {db_names}")
        self.store.write_json(
            self.store.instance_dir(instance) / "databases.json",
            {"instance": instance, "databases": [{"name": db} for db in db_names]},
        )

        # Step 3: Build databases in parallel
        # Dependency: databases are independent, can be parallel
        database_docs = self._build_databases_parallel(instance, db_names, options, stats, profiler)

        # Step 4: Write instance-level documents (depends on all databases)
        self.store.write_json(
            self.store.instance_dir(instance) / "databases.json",
            {
                "instance": instance,
                "databases": [
                    {"name": db.get("name"), "description": db.get("description"), "table_count": db.get("table_count")}
                    for db in database_docs
                ],
            },
        )
        instance_doc = self.summarizer.instance_doc(instance=instance, databases=database_docs)
        instance_doc["built_at"] = started
        instance_doc["completed_at"] = time.time()
        instance_doc["connection_type"] = self.connection.type
        instance_doc["database_count"] = len(database_docs)
        instance_doc["build_options"] = asdict(options)
        instance_doc["stats"] = asdict(stats)
        instance_doc["asset_root"] = str(self.store.instance_dir(instance))
        self.store.write_json(self.store.instance_dir(instance) / "instance.json", instance_doc)
        self.store.write_json(
            self.store.instance_dir(instance) / "manifest.json",
            {
                "asset_schema_version": 2,
                "instance": instance,
                "built_at": started,
                "completed_at": instance_doc["completed_at"],
                "connection_type": self.connection.type,
                "databases": [db.get("name") for db in database_docs],
                "options": asdict(options),
                "stats": asdict(stats),
            },
        )
        stats.elapsed_seconds = time.time() - started
        self.progress(
            f"[assets] completed {instance}: db={stats.databases}, tables={stats.tables}, "
            f"columns={stats.columns}, profiled={stats.profiled_columns}, "
            f"errors={len(stats.errors)}, elapsed={stats.elapsed_seconds:.1f}s"
        )
        return stats

    def _resolve_databases(self, databases: list[str] | None) -> list[str]:
        if databases:
            return databases
        return self.adapter.list_databases()

    def _build_databases_parallel(self, instance: str, db_names: list[str], options: BuildOptions,
                                  stats: BuildStats, profiler: ColumnProfiler) -> list[dict]:
        """Build databases in parallel. Databases are independent."""
        database_docs = []
        if len(db_names) <= 1:
            # Single database, no need for parallel
            for database in db_names:
                if self._is_expired(options.deadline):
                    self.progress(f"[assets] time budget exhausted, skipping database {database}")
                    stats.errors.append(f"{instance}.{database}: skipped (time budget)")
                    continue
                try:
                    doc = self._build_database(instance, database, options=options, stats=stats, profiler=profiler)
                    database_docs.append(doc)
                except Exception as exc:
                    stats.errors.append(f"{instance}.{database}: {type(exc).__name__}: {exc}")
            return database_docs

        # Multiple databases, build in parallel
        with ThreadPoolExecutor(max_workers=min(options.max_workers, len(db_names))) as executor:
            futures = {}
            for database in db_names:
                if self._is_expired(options.deadline):
                    self.progress(f"[assets] time budget exhausted, skipping database {database}")
                    stats.errors.append(f"{instance}.{database}: skipped (time budget)")
                    continue
                future = executor.submit(self._build_database, instance, database,
                                         options=options, stats=stats, profiler=profiler)
                futures[future] = database

            for future in as_completed(futures):
                database = futures[future]
                try:
                    doc = future.result()
                    database_docs.append(doc)
                except Exception as exc:
                    stats.errors.append(f"{instance}.{database}: {type(exc).__name__}: {exc}")

        return database_docs

    def _build_database(self, instance: str, database: str, *, options: BuildOptions,
                        stats: BuildStats, profiler: ColumnProfiler) -> dict:
        self.progress(f"[assets] listing tables {instance}.{database}")
        stats.databases += 1
        tables = self.adapter.list_tables(database=database)
        self.progress(f"[assets] database={database}, found {len(tables)} table(s)")

        # Build tables in parallel
        # Dependency: tables are independent within a database
        table_docs = self._build_tables_parallel(instance, database, tables, options, stats, profiler)

        # Write database-level document (depends on all tables)
        self.store.write_json(
            self.store.database_dir(instance, database) / "tables.json",
            {"instance": instance, "database": database, "tables": table_docs},
        )
        database_doc = self.summarizer.database_doc(instance=instance, database=database, tables=table_docs)
        database_doc["table_count"] = len(table_docs)
        database_doc["build_options"] = asdict(options)
        self.store.write_json(self.store.database_dir(instance, database) / "database.json", database_doc)
        return database_doc

    def _build_tables_parallel(self, instance: str, database: str, tables: list, options: BuildOptions,
                               stats: BuildStats, profiler: ColumnProfiler) -> list[dict]:
        """Build tables in parallel. Tables are independent within a database."""
        table_docs = []
        if len(tables) <= 1:
            # Single table, no need for parallel
            for table in tables:
                if self._is_expired(options.deadline):
                    self.progress(f"[assets] time budget exhausted, skipping table {table.name}")
                    stats.errors.append(f"{instance}.{database}.{table.name}: skipped (time budget)")
                    continue
                try:
                    doc = self._build_table(instance, database, table, options=options,
                                            stats=stats, profiler=profiler)
                    table_docs.append(doc)
                except Exception as exc:
                    stats.errors.append(f"{instance}.{database}.{table.name}: {type(exc).__name__}: {exc}")
            return table_docs

        # Multiple tables, build in parallel
        with ThreadPoolExecutor(max_workers=min(options.max_workers, len(tables))) as executor:
            futures = {}
            for table in tables:
                if self._is_expired(options.deadline):
                    self.progress(f"[assets] time budget exhausted, skipping table {table.name}")
                    stats.errors.append(f"{instance}.{database}.{table.name}: skipped (time budget)")
                    continue
                future = executor.submit(self._build_table, instance, database, table,
                                         options=options, stats=stats, profiler=profiler)
                futures[future] = table

            for future in as_completed(futures):
                table = futures[future]
                try:
                    doc = future.result()
                    table_docs.append(doc)
                except Exception as exc:
                    stats.errors.append(f"{instance}.{database}.{table.name}: {type(exc).__name__}: {exc}")

        return table_docs

    def _build_table(self, instance: str, database: str, table, *, options: BuildOptions,
                     stats: BuildStats, profiler: ColumnProfiler) -> dict:
        self.progress(f"[assets] describing {instance}.{database}.{table.name}")
        stats.tables += 1
        columns = self.adapter.describe_table(table.name, database=database)
        foreign_keys = self.adapter.foreign_keys(table.name, database=database)

        # Get sample rows
        sample_rows = []
        if options.sample:
            try:
                sample_rows = self.adapter.sample_rows(
                    table.name, database=database,
                    limit=min(options.sample_limit, max(20, options.top_k)),
                ).rows
            except Exception as exc:
                stats.errors.append(f"{instance}.{database}.{table.name}.sample: {type(exc).__name__}: {exc}")

        # Build columns in parallel
        # Dependency: columns are independent within a table
        column_docs = self._build_columns_parallel(instance, database, table.name, columns, options, stats, profiler)

        # Write table-level document (depends on all columns)
        table_doc = self.summarizer.table_doc(
            instance=instance, database=database, table=table,
            columns=column_docs, foreign_keys=foreign_keys,
        )
        table_doc["sample_rows"] = sample_rows
        table_doc["column_count"] = len(column_docs)
        self.store.write_json(self.store.table_dir(instance, database, table.name) / "table.json", table_doc)
        self.store.write_json(
            self.store.table_dir(instance, database, table.name) / "columns.json",
            {"instance": instance, "database": database, "table": table.name, "columns": column_docs},
        )
        return table_doc

    def _build_columns_parallel(self, instance: str, database: str, table_name: str,
                                columns: list, options: BuildOptions,
                                stats: BuildStats, profiler: ColumnProfiler) -> list[dict]:
        """Build columns in parallel. Columns are independent within a table."""
        column_docs = []

        def _build_one_column(column):
            if self._is_expired(options.deadline):
                return self._build_column_expired(instance, database, table_name, column, stats)
            profile_obj = None
            if should_profile_column(column, mode=options.profile_mode):
                try:
                    profile_obj = profiler.profile(
                        table_name, column, database=database,
                        top_k=options.top_k, sample_limit=options.sample_limit,
                    )
                except Exception as exc:
                    stats.errors.append(
                        f"{instance}.{database}.{table_name}.{column.name}: {type(exc).__name__}: {exc}"
                    )
            else:
                stats.skipped_profiles += 1
            doc = self.summarizer.column_doc(instance=instance, database=database,
                                              table=table_name, column=column, profile=profile_obj)
            self.store.write_json(
                self.store.column_dir(instance, database, table_name) / f"{column.name}.json", doc
            )
            stats.columns += 1
            if profile_obj:
                stats.profiled_columns += 1
            return doc

        if len(columns) <= 2:
            # Few columns, no need for parallel
            for column in columns:
                doc = _build_one_column(column)
                column_docs.append(doc)
            return column_docs

        # Multiple columns, build in parallel
        with ThreadPoolExecutor(max_workers=min(options.max_workers, len(columns))) as executor:
            futures = {executor.submit(_build_one_column, col): col for col in columns}
            for future in as_completed(futures):
                try:
                    doc = future.result()
                    column_docs.append(doc)
                except Exception as exc:
                    column = futures[future]
                    stats.errors.append(
                        f"{instance}.{database}.{table_name}.{column.name}: {type(exc).__name__}: {exc}"
                    )

        return column_docs

    def _build_column_expired(self, instance: str, database: str, table_name: str, column, stats: BuildStats) -> dict:
        stats.skipped_profiles += 1
        stats.timed_out_columns += 1
        doc = self.summarizer.column_doc(instance=instance, database=database,
                                          table=table_name, column=column, profile=None)
        self.store.write_json(
            self.store.column_dir(instance, database, table_name) / f"{column.name}.json", doc
        )
        stats.columns += 1
        return doc

    @staticmethod
    def _is_expired(deadline: float) -> bool:
        if deadline <= 0:
            return False
        return time.time() >= deadline


def normalize_profile_mode(mode: str) -> str:
    mode = str(mode or "auto").lower().strip()
    if mode in {"none", "no", "false", "off", "skip"}:
        return "none"
    if mode in {"all", "full", "true", "on"}:
        return "all"
    return "auto"


def should_profile_column(column, *, mode: str) -> bool:
    mode = normalize_profile_mode(mode)
    if mode == "none":
        return False
    if mode == "all":
        return True
    name = str(column.name or "").lower()
    typ = str(column.data_type or "").lower()
    if column.primary_key or column.indexed:
        return True
    if name == "id" or name.endswith("_id"):
        return True
    if any(k in name for k in ["status", "state", "type", "category", "kind", "level"]):
        return True
    if any(k in name for k in ["created", "updated"]) or name.endswith(("_at", "_time", "_date")):
        return True
    if any(k in name for k in ["amount", "price", "total", "fee", "cost", "money", "count", "qty", "quantity"]):
        return True
    if any(k in typ for k in ["date", "time", "bool"]):
        return True
    if any(k in typ for k in ["int", "real", "numeric", "decimal", "float", "double"]):
        return True
    return False
