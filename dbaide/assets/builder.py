from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Callable

from dbaide.adapters.base import DatabaseAdapter
from dbaide.assets.profiler import ColumnProfiler
from dbaide.assets.store import AssetStore
from dbaide.assets.summarizer import ASSET_SCHEMA_VERSION, AssetSummarizer
from dbaide.llm import LLMClient
from dbaide.models import ConnectionConfig

logger = logging.getLogger("dbaide.builder")

# Trace node id for the build root (databases hang under it).
_BUILD_ROOT = "build:root"


@dataclass(slots=True)
class BuildStats:
    instances: int = 0
    databases: int = 0
    tables: int = 0
    columns: int = 0
    profiled_columns: int = 0
    skipped_profiles: int = 0
    timed_out_columns: int = 0
    light_tables: int = 0          # tables profiled in metadata-only (light) mode due to size
    total_queries: int = 0         # SQL statements issued against the database
    peak_inflight: int = 0         # peak concurrent queries observed
    estimated_queries: int = 0     # populated by dry-run
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
    max_workers: int = 1
    big_table_rows: int = 1_000_000
    dry_run: bool = False


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
        # Tables build on a shared thread pool and mutate one BuildStats; guard the
        # counter/error updates so they're correct even under free-threaded Python.
        self._stats_lock = threading.Lock()

    def _bump(self, stats: BuildStats, **deltas: int) -> None:
        with self._stats_lock:
            for name, delta in deltas.items():
                setattr(stats, name, getattr(stats, name) + delta)

    def _record_error(self, stats: BuildStats, message: str) -> None:
        with self._stats_lock:
            stats.errors.append(message)

    # ── Structured progress for the trace (root → per-database nodes) ─────────

    def _emit(self, title: str, *, node_id: str = _BUILD_ROOT, parent_id: str = "",
              status: str = "running", kind: str = "tool", duration_ms: float = 0.0) -> None:
        """Emit a structured build-progress event. The GUI renders these as a tree
        (Building assets → each database); string consumers (CLI) read ``title``."""
        event: dict = {"stage": "build_assets", "title": title, "status": status,
                       "kind": kind, "node_id": node_id}
        if parent_id:
            event["parent_id"] = parent_id
        if duration_ms:
            event["duration_ms"] = duration_ms
        self.progress(event)

    def _emit_db(self, database: str, title: str, *, status: str = "running") -> None:
        self._emit(title, node_id=f"build:db:{database}", parent_id=_BUILD_ROOT,
                   status=status, kind="substep")

    def build(
        self,
        *,
        databases: list[str] | None = None,
        sample: bool = True,
        profile: bool | None = None,
        profile_mode: str | None = None,
        top_k: int = 30,
        sample_limit: int = 50,
        timeout: int = 0,
        per_column_timeout: int = 30,
        max_workers: int | None = None,
        dry_run: bool = False,
    ) -> BuildStats:
        started = time.time()
        deadline = started + timeout if timeout > 0 else 0.0
        # Build runs under the "build" caller tag and its own conservative policy.
        self.adapter.caller = "build"
        policy = self.adapter.policy
        if profile is not None:
            profile_mode = "all" if profile else "none"
        if profile_mode is None:
            profile_mode = policy.build_profile_mode
        workers = max_workers if (max_workers and max_workers > 0) else policy.build_max_workers
        options = BuildOptions(
            sample=sample,
            profile_mode=normalize_profile_mode(profile_mode),
            top_k=top_k,
            sample_limit=sample_limit,
            per_column_timeout=per_column_timeout,
            deadline=deadline,
            max_workers=max(1, int(workers)),
            big_table_rows=policy.big_table_rows,
            dry_run=dry_run,
        )
        profiler = ColumnProfiler(self.adapter, timeout_seconds=per_column_timeout)
        stats = BuildStats(instances=1)
        instance = self.connection.name
        # Reset the shared budget stats so total_queries / peak reflect this build.
        try:
            self.adapter.budget.reset_stats()
        except Exception:  # pragma: no cover - defensive
            pass

        # Step 1: Test connection
        self._emit(f"Building assets · {instance}", status="running")
        self._emit(f"testing connection {instance}", status="running")
        self.adapter.test()

        if options.dry_run:
            return self._dry_run(instance, databases, options, stats)

        # Step 2: Discover databases
        partial = bool(databases)
        preserved_docs = self._load_existing_database_docs(instance) if partial else []
        db_names = self._resolve_databases(databases)
        self._emit(f"discovered {len(db_names)} database(s): {', '.join(db_names)}", status="running")
        if not partial:
            self.store.write_json(
                self.store.instance_dir(instance) / "databases.json",
                {"instance": instance, "databases": [{"name": db} for db in db_names]},
            )

        # Step 3: Build databases. A single shared worker pool (sized by the
        # resource policy) parallelises tables; columns are profiled serially.
        # Real DB concurrency is additionally capped by the QueryBudget semaphore.
        with ThreadPoolExecutor(max_workers=options.max_workers) as executor:
            database_docs = self._build_databases(instance, db_names, options, stats, profiler, executor)
        database_docs = self._merge_database_docs(
            built_docs=database_docs,
            partial=partial,
            preserved_docs=preserved_docs,
        )

        # Step 4: Write instance-level documents (depends on all databases)
        existing_instance = self.store.instance_doc(instance) if partial else None
        built_at = float(existing_instance.get("built_at") or started) if existing_instance else started
        instance_stats = self._instance_stats(instance, database_docs, build_stats=stats, partial=partial)

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
        instance_doc["built_at"] = built_at
        instance_doc["completed_at"] = time.time()
        instance_doc["connection_type"] = self.connection.type
        instance_doc["database_count"] = len(database_docs)
        instance_doc["build_options"] = asdict(options)
        instance_doc["stats"] = instance_stats
        instance_doc["asset_root"] = str(self.store.instance_dir(instance))
        if partial:
            instance_doc["last_build"] = {
                "databases": db_names,
                "completed_at": instance_doc["completed_at"],
                "stats": asdict(stats),
            }
        self.store.write_json(self.store.instance_dir(instance) / "instance.json", instance_doc)
        self.store.write_json(
            self.store.instance_dir(instance) / "manifest.json",
            {
                "asset_schema_version": ASSET_SCHEMA_VERSION,
                "instance": instance,
                "built_at": built_at,
                "completed_at": instance_doc["completed_at"],
                "connection_type": self.connection.type,
                "databases": [db.get("name") for db in database_docs],
                "options": asdict(options),
                "stats": instance_stats,
                "last_build_stats": asdict(stats) if partial else None,
            },
        )
        stats.elapsed_seconds = time.time() - started
        try:
            budget_stats = self.adapter.budget.stats
            stats.total_queries = budget_stats.total_queries
            stats.peak_inflight = budget_stats.peak_inflight
        except Exception:  # pragma: no cover - defensive
            pass
        summary = (
            f"{stats.tables} tables · {stats.columns} columns · {stats.profiled_columns} profiled"
            + (f" · {stats.light_tables} light" if stats.light_tables else "")
            + f" · {stats.total_queries} queries · peak {stats.peak_inflight}"
            + (f" · {len(stats.errors)} errors" if stats.errors else "")
        )
        self._emit(summary, status="failed" if stats.errors else "completed",
                   duration_ms=stats.elapsed_seconds * 1000)
        return stats

    def _dry_run(self, instance: str, databases: list[str] | None, options: BuildOptions,
                 stats: BuildStats) -> BuildStats:
        """Estimate the query count without running any profiling SQL.

        Only cheap metadata calls (list/describe/foreign_keys) touch the database;
        profiling and sampling are counted but not executed.
        """
        self._emit(f"dry-run estimate for {instance}", status="running")
        db_names = self._resolve_databases(databases)
        # SQLite folds count/distinct/top-K/sample into a single guarded query, so a
        # profiled column costs 2 queries (profile + context sample) regardless of tier.
        # MySQL/Postgres issue the top-K as a separate query, so heavy = 3, light = 2.
        sqlite = self.adapter.dialect == "sqlite"
        estimated = 0
        for database in db_names:
            stats.databases += 1
            tables = self.adapter.list_tables(database=database)
            for table in tables:
                stats.tables += 1
                columns = self.adapter.describe_table(table.name, database=database)
                stats.columns += len(columns)
                heavy = self._is_heavy(table, options)
                if options.sample:
                    estimated += 1  # sample_rows
                for column in columns:
                    if should_profile_column(column, mode=options.profile_mode):
                        estimated += 2 if (sqlite or not heavy) else 3
                        stats.profiled_columns += 1
                    else:
                        stats.skipped_profiles += 1
        stats.estimated_queries = estimated
        stats.elapsed_seconds = 0.0
        self._emit(
            f"dry-run · {stats.tables} tables · {stats.columns} columns · "
            f"would profile {stats.profiled_columns} · ≈{estimated} queries",
            status="completed",
        )
        return stats

    @staticmethod
    def _is_heavy(table, options: BuildOptions) -> bool:
        rows = getattr(table, "estimated_rows", None)
        if options.big_table_rows > 0 and rows is not None and rows > options.big_table_rows:
            return False
        return True

    def _resolve_databases(self, databases: list[str] | None) -> list[str]:
        if databases:
            return databases
        return self.adapter.list_databases()

    def _load_existing_database_docs(self, instance: str) -> list[dict]:
        docs: list[dict] = []
        for entry in self.store.database_docs(instance):
            name = str(entry.get("name") or "")
            if not name:
                continue
            path = self.store.database_dir(instance, name) / "database.json"
            doc = self.store._read_optional(path)
            if isinstance(doc, dict):
                docs.append(doc)
        return docs

    def _merge_database_docs(
        self,
        *,
        built_docs: list[dict],
        partial: bool,
        preserved_docs: list[dict] | None = None,
    ) -> list[dict]:
        if not partial:
            return built_docs
        built_names = {str(doc.get("name") or doc.get("database") or "") for doc in built_docs}
        merged: dict[str, dict] = {}
        for doc in preserved_docs or []:
            name = str(doc.get("name") or doc.get("database") or "")
            if name and name not in built_names:
                merged[name] = doc
        for doc in built_docs:
            name = str(doc.get("name") or doc.get("database") or "")
            if name:
                merged[name] = doc
        return [merged[name] for name in sorted(merged.keys())]

    def _instance_stats(
        self,
        instance: str,
        database_docs: list[dict],
        *,
        build_stats: BuildStats,
        partial: bool,
    ) -> dict:
        tables = 0
        columns = 0
        for db_doc in database_docs:
            db_name = str(db_doc.get("name") or db_doc.get("database") or "")
            if not db_name:
                continue
            for table_doc in self.store.table_docs(instance, db_name):
                tables += 1
                columns += int(table_doc.get("column_count") or len(table_doc.get("columns") or []))
        stats = {
            "instances": 1,
            "databases": len(database_docs),
            "tables": tables,
            "columns": columns,
            "profiled_columns": self._count_profiled_columns(instance, database_docs),
            "skipped_profiles": build_stats.skipped_profiles,
            "timed_out_columns": build_stats.timed_out_columns,
            "errors": list(build_stats.errors),
            "elapsed_seconds": build_stats.elapsed_seconds,
        }
        if partial:
            prior_errors = (self.store.instance_doc(instance) or {}).get("stats", {}).get("errors") or []
            if isinstance(prior_errors, list):
                stats["errors"] = list(prior_errors) + list(build_stats.errors)
        return stats

    def _count_profiled_columns(self, instance: str, database_docs: list[dict]) -> int:
        count = 0
        for db_doc in database_docs:
            db_name = str(db_doc.get("name") or db_doc.get("database") or "")
            if not db_name:
                continue
            for table_doc in self.store.table_docs(instance, db_name):
                table_name = str(table_doc.get("name") or table_doc.get("table") or "")
                if not table_name:
                    continue
                for col_doc in self.store.column_docs(instance, db_name, table_name):
                    if col_doc.get("profile_status") == "profiled":
                        count += 1
        return count

    def _build_databases(self, instance: str, db_names: list[str], options: BuildOptions,
                         stats: BuildStats, profiler: ColumnProfiler, executor: ThreadPoolExecutor) -> list[dict]:
        """Build databases serially; tables within each database run on the shared pool."""
        database_docs = []
        for database in db_names:
            if self._is_expired(options.deadline):
                self._emit_db(database, f"{database}: skipped (time budget)", status="failed")
                self._record_error(stats, f"{instance}.{database}: skipped (time budget)")
                continue
            try:
                doc = self._build_database(instance, database, options=options, stats=stats,
                                           profiler=profiler, executor=executor)
                database_docs.append(doc)
            except Exception as exc:
                self._record_error(stats, f"{instance}.{database}: {type(exc).__name__}: {exc}")
        return database_docs

    def _build_database(self, instance: str, database: str, *, options: BuildOptions,
                        stats: BuildStats, profiler: ColumnProfiler, executor: ThreadPoolExecutor) -> dict:
        self._emit_db(database, f"{database} · listing tables…", status="running")
        self._bump(stats, databases=1)
        tables = self.adapter.list_tables(database=database)
        total = len(tables)
        self._emit_db(database, f"{database} · {total} tables", status="running")

        # Tables are independent within a database; fan them out onto the shared pool.
        table_docs: list[dict] = []
        futures = {}
        for table in tables:
            if self._is_expired(options.deadline):
                self._record_error(stats, f"{instance}.{database}.{table.name}: skipped (time budget)")
                continue
            futures[executor.submit(self._build_table, instance, database, table,
                                    options=options, stats=stats, profiler=profiler)] = table
        done = 0
        for future in as_completed(futures):
            table = futures[future]
            done += 1
            try:
                table_docs.append(future.result())
            except Exception as exc:
                self._record_error(stats, f"{instance}.{database}.{table.name}: {type(exc).__name__}: {exc}")
            self._emit_db(database, f"{database} · {done}/{total} tables · {table.name}", status="running")
        cols = sum(int(td.get("column_count") or 0) for td in table_docs)
        self._emit_db(database, f"{database} · {len(table_docs)} tables · {cols} columns", status="completed")

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

    def _build_table(self, instance: str, database: str, table, *, options: BuildOptions,
                     stats: BuildStats, profiler: ColumnProfiler) -> dict:
        self._emit_db(database, f"{database} · describing {table.name}…", status="running")
        self._bump(stats, tables=1)
        columns = self.adapter.describe_table(table.name, database=database)
        foreign_keys = self.adapter.foreign_keys(table.name, database=database)

        # Large tables drop to metadata-only profiling so we never full-scan them.
        heavy = self._is_heavy(table, options)
        if not heavy:
            self._bump(stats, light_tables=1)

        # Get sample rows (bounded by LIMIT — cheap even on big tables)
        sample_rows = []
        if options.sample:
            try:
                sample_rows = self.adapter.sample_rows(
                    table.name, database=database,
                    limit=min(options.sample_limit, max(20, options.top_k)),
                ).rows
            except Exception as exc:
                self._record_error(stats, f"{instance}.{database}.{table.name}.sample: {type(exc).__name__}: {exc}")

        # Columns are profiled serially within the table; the shared pool already
        # gives table-level parallelism and the QueryBudget caps real DB concurrency.
        column_docs: list[dict] = []
        for column in columns:
            column_docs.append(
                self._build_column(instance, database, table.name, column, options, stats, profiler, heavy=heavy)
            )

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

    def _build_column(self, instance: str, database: str, table_name: str, column,
                      options: BuildOptions, stats: BuildStats, profiler: ColumnProfiler,
                      *, heavy: bool) -> dict:
        if self._is_expired(options.deadline):
            return self._build_column_expired(instance, database, table_name, column, stats)
        profile_obj = None
        if should_profile_column(column, mode=options.profile_mode):
            try:
                profile_obj = profiler.profile(
                    table_name, column, database=database,
                    top_k=options.top_k, sample_limit=options.sample_limit,
                    heavy_scan=heavy,
                )
            except Exception as exc:
                self._record_error(
                    stats, f"{instance}.{database}.{table_name}.{column.name}: {type(exc).__name__}: {exc}"
                )
        else:
            self._bump(stats, skipped_profiles=1)
        doc = self.summarizer.column_doc(instance=instance, database=database,
                                          table=table_name, column=column, profile=profile_obj)
        self.store.write_json(
            self.store.column_dir(instance, database, table_name) / f"{column.name}.json", doc
        )
        self._bump(stats, columns=1, profiled_columns=1 if profile_obj else 0)
        return doc

    def _build_column_expired(self, instance: str, database: str, table_name: str, column, stats: BuildStats) -> dict:
        self._bump(stats, skipped_profiles=1, timed_out_columns=1)
        doc = self.summarizer.column_doc(instance=instance, database=database,
                                          table=table_name, column=column, profile=None)
        self.store.write_json(
            self.store.column_dir(instance, database, table_name) / f"{column.name}.json", doc
        )
        self._bump(stats, columns=1)
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
    if mode in {"light", "minimal", "lite"}:
        return "light"
    if mode in {"all", "full", "true", "on"}:
        return "all"
    return "auto"


def should_profile_column(column, *, mode: str) -> bool:
    mode = normalize_profile_mode(mode)
    if mode == "none":
        return False
    if mode == "all":
        return True
    if mode == "light":
        # Only structurally important columns: PK / indexed (FK) / documented.
        return bool(column.primary_key or column.indexed or (column.comment or "").strip())
    # auto: keys, temporal, boolean and numeric columns.
    if column.primary_key or column.indexed:
        return True
    typ = str(column.data_type or "").lower()
    if any(k in typ for k in ["date", "time", "timestamp", "bool", "bit"]):
        return True
    if any(k in typ for k in ["int", "real", "numeric", "decimal", "float", "double", "number"]):
        return True
    return False
