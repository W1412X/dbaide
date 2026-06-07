from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Callable

from dbaide.adapters.base import DatabaseAdapter
from dbaide.assets.profiler import kind_from_type
from dbaide.assets.store import AssetStore

if TYPE_CHECKING:
    from dbaide.joins import JoinCatalogStore
from dbaide.assets.summarizer import ASSET_SCHEMA_VERSION, AssetSummarizer
from dbaide.llm import LLMClient
from dbaide.models import ConnectionConfig, TableInfo

logger = logging.getLogger("dbaide.builder")

# Trace node id for the build root (databases hang under it).
_BUILD_ROOT = "build:root"

# Cap how many queries we inline into a table's trace detail (a wide table can run
# hundreds); the full audit always remains in the query-log jsonl.
_MAX_TRACE_QUERIES = 40


def _format_build_queries(entries: list) -> str:
    """Render captured QueryLogEntry rows as an annotated SQL block for the trace."""
    lines: list[str] = []
    for entry in entries[:_MAX_TRACE_QUERIES]:
        status = getattr(entry, "status", "ok")
        elapsed = float(getattr(entry, "elapsed_ms", 0.0) or 0.0)
        rows = int(getattr(entry, "row_count", 0) or 0)
        lines.append(f"-- {status} · {elapsed:.0f}ms · {rows} rows")
        lines.append(str(getattr(entry, "sql", "")).strip())
        lines.append("")
    if len(entries) > _MAX_TRACE_QUERIES:
        lines.append(f"… and {len(entries) - _MAX_TRACE_QUERIES} more quer"
                     f"{'y' if len(entries) - _MAX_TRACE_QUERIES == 1 else 'ies'}")
    return "\n".join(lines).strip()


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
    collect_row_counts: bool = True
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
        join_catalog: "JoinCatalogStore | None" = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.adapter = adapter
        self.store = store or AssetStore()
        self.summarizer = AssetSummarizer(llm)
        # When provided, declared foreign keys discovered during the build are saved
        # to the join catalog (collected here under a lock, persisted once at the end
        # to avoid a read-modify-write race across the parallel table workers).
        self.join_catalog = join_catalog
        self._fk_relations: list[dict] = []
        self.progress = progress or (lambda _msg: None)
        # Tables build on a shared thread pool and mutate one BuildStats; guard the
        # counter/error updates so they're correct even under free-threaded Python.
        self._stats_lock = threading.Lock()
        # Per-table SQL capture: each worker builds one table start-to-finish, so a
        # thread-local bucket fed by a QueryLog subscriber attributes every query to
        # its table. Workers stash their captured queries here; the main thread reads
        # them when it emits the table's trace node.
        self._tls = threading.local()
        self._table_sql: dict[str, list] = {}
        self._table_sql_lock = threading.Lock()

    def _on_query_logged(self, entry) -> None:
        """QueryLog subscriber — runs in the thread that executed the SQL."""
        bucket = getattr(self._tls, "bucket", None)
        if bucket is not None:
            bucket.append(entry)

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

    def _persist_fk_joins(self, instance: str) -> None:
        """Save the declared foreign keys found during the build as join edges, so a
        relation discovered from the schema is immediately part of the join catalog."""
        if self.join_catalog is None or not self._fk_relations:
            return
        rels, self._fk_relations = self._fk_relations, []
        saved = 0
        for rel in rels:
            try:
                self.join_catalog.add(
                    instance,
                    rel,
                    source="foreign_key",
                    database=rel.get("database") or "",
                    fingerprint=self.store.connection_metadata(self.connection)["connection_fingerprint"],
                )
                saved += 1
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("fk join persist failed: %s", exc)
        if saved:
            self._emit(f"saved {saved} foreign-key join(s) to the catalog", status="running")

    def _emit_table(self, database: str, table: str, *, status: str, note: str = "") -> None:
        """Emit/refresh one table's trace node. Called live from the worker as the
        table runs (status=running) and once at the end (completed/failed), so the
        trace shows which table is active, what it has run, and what it's running now.
        The node carries the queries captured so far, so clicking it shows them."""
        entries = list(getattr(self._tls, "bucket", None) or [])
        ok = sum(1 for e in entries if getattr(e, "status", "ok") == "ok")
        errs = len(entries) - ok
        total_rows = sum(int(getattr(e, "row_count", 0) or 0) for e in entries)
        count = f"{len(entries)} quer{'y' if len(entries) == 1 else 'ies'}"
        fail_suffix = f" · {errs} failed" if errs else ""
        if status == "running":
            title = f"{table} · {note}" if note else f"{table} · {count}"
        else:
            title = f"{table} · {count}{fail_suffix}"
        event: dict = {
            "stage": "build_assets",
            "title": title,
            "status": status,
            "kind": "sql",
            "node_id": f"build:table:{database}.{table}",
            "parent_id": f"build:db:{database}",
            "row_count": total_rows,
            "database": database,
        }
        sql_text = _format_build_queries(entries)
        if sql_text:
            event["sql"] = sql_text
        self.progress(event)

    def build(
        self,
        *,
        databases: list[str] | None = None,
        tables: list[str] | None = None,
        sample: bool = True,
        collect_row_counts: bool = True,
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
            collect_row_counts=bool(collect_row_counts),
            profile_mode=normalize_profile_mode(profile_mode),
            top_k=top_k,
            sample_limit=sample_limit,
            per_column_timeout=per_column_timeout,
            deadline=deadline,
            max_workers=max(1, int(workers)),
            big_table_rows=policy.big_table_rows,
            dry_run=dry_run,
        )
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

        # Table-level build: rebuild only the named tables within their database,
        # preserving every other table's existing doc (used for granular enrichment).
        # It requires an explicit database so the filter is unambiguous.
        only_tables = {str(t) for t in tables if str(t)} if tables else None
        if only_tables and not databases:
            raise ValueError("table-level build requires an explicit database")

        # Step 2: Discover databases
        partial = bool(databases or tables)
        preserved_docs = self._load_existing_database_docs(instance) if partial else []
        db_names = self._resolve_databases(databases)
        self._emit(f"discovered {len(db_names)} database(s): {', '.join(db_names)}", status="running")
        if not partial:
            self.store.write_json(
                self.store.instance_dir(instance) / "databases.json",
                {
                    "instance": instance,
                    **self.store.connection_metadata(self.connection),
                    "databases": [{"name": db} for db in db_names],
                },
            )

        # Step 3: Build databases. A single shared worker pool (sized by the
        # resource policy) parallelises tables; columns are profiled serially.
        # Real DB concurrency is additionally capped by the QueryBudget semaphore.
        # Subscribe to the query log so each table's trace node can show its SQL.
        unsubscribe = self.adapter.query_log.subscribe(self._on_query_logged)
        try:
            with ThreadPoolExecutor(max_workers=options.max_workers) as executor:
                database_docs = self._build_databases(instance, db_names, options, stats, executor,
                                                      only_tables=only_tables)
        finally:
            unsubscribe()
        self._persist_fk_joins(instance)
        database_docs = self._merge_database_docs(
            built_docs=database_docs,
            partial=partial,
            preserved_docs=preserved_docs,
        )

        # Step 4: Write instance-level documents (depends on all databases)
        existing_instance = self.store.instance_doc(instance, connection=self.connection) if partial else None
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
        instance_doc.update(self.store.connection_metadata(self.connection))
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
                **self.store.connection_metadata(self.connection),
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
        # The table is the leaf now: each table costs at most a COUNT(*) (skipped for
        # heavy tables) + a sample query. No per-column profiling.
        estimated = 0
        for database in db_names:
            stats.databases += 1
            tables = self.adapter.list_tables(database=database)
            for table in tables:
                stats.tables += 1
                columns = self.adapter.describe_table(self._table_key(table), database=database)
                stats.columns += len(columns)
                scannable = self._is_heavy(table, options)
                if options.collect_row_counts and scannable:
                    estimated += 1  # COUNT(*)
                if options.sample:
                    estimated += 1  # sample_rows
        stats.estimated_queries = estimated
        stats.elapsed_seconds = 0.0
        self._emit(
            f"dry-run · {stats.tables} tables · {stats.columns} columns · ≈{estimated} queries",
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
        for entry in self.store.database_docs(instance, connection=self.connection):
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
            for table_doc in self.store.table_docs(instance, db_name, connection=self.connection):
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
            prior_errors = (self.store.instance_doc(instance, connection=self.connection) or {}).get("stats", {}).get("errors") or []
            if isinstance(prior_errors, list):
                stats["errors"] = list(prior_errors) + list(build_stats.errors)
        return stats

    def _count_profiled_columns(self, instance: str, database_docs: list[dict]) -> int:
        # No offline per-column profiling anymore — column stats are fetched on demand.
        return 0

    def _build_databases(self, instance: str, db_names: list[str], options: BuildOptions,
                         stats: BuildStats, executor: ThreadPoolExecutor,
                         *, only_tables: set[str] | None = None) -> list[dict]:
        """Build databases serially; tables within each database run on the shared pool."""
        database_docs = []
        for database in db_names:
            if self._is_expired(options.deadline):
                self._emit_db(database, f"{database}: skipped (time budget)", status="failed")
                self._record_error(stats, f"{instance}.{database}: skipped (time budget)")
                continue
            try:
                doc = self._build_database(instance, database, options=options, stats=stats,
                                           executor=executor, only_tables=only_tables)
                database_docs.append(doc)
            except Exception as exc:
                self._record_error(stats, f"{instance}.{database}: {type(exc).__name__}: {exc}")
        return database_docs

    def _build_database(self, instance: str, database: str, *, options: BuildOptions,
                        stats: BuildStats, executor: ThreadPoolExecutor,
                        only_tables: set[str] | None = None) -> dict:
        self._emit_db(database, f"{database} · listing tables…", status="running")
        self._bump(stats, databases=1)
        tables = self.adapter.list_tables(database=database)
        # Table-level build: rebuild only the targeted tables; the rest keep their
        # existing docs so the database/instance rollup stays complete.
        preserved_table_docs: list[dict] = []
        if only_tables is not None:
            preserved_table_docs = [
                td for td in self.store.table_docs(instance, database, connection=self.connection)
                if str(td.get("name") or td.get("table") or "") not in only_tables
            ]
            tables = [t for t in tables if self._table_key(t) in only_tables or t.name in only_tables]
        total = len(tables)
        self._emit_db(database, f"{database} · {total} tables", status="running")

        # Tables are independent within a database; fan them out onto the shared pool.
        table_docs: list[dict] = []
        futures = {}
        for table in tables:
            if self._is_expired(options.deadline):
                self._record_error(stats, f"{instance}.{database}.{self._table_key(table)}: skipped (time budget)")
                continue
            futures[executor.submit(self._build_table, instance, database, table,
                                    options=options, stats=stats)] = table
        done = 0
        for future in as_completed(futures):
            table = futures[future]
            done += 1
            try:
                table_docs.append(future.result())
            except Exception as exc:
                self._record_error(stats, f"{instance}.{database}.{self._table_key(table)}: {type(exc).__name__}: {exc}")
            # The table node itself is emitted live from the worker; here we only
            # advance the database-level progress counter.
            self._emit_db(database, f"{database} · {done}/{total} tables · {self._table_key(table)}", status="running")
        if preserved_table_docs:
            table_docs = table_docs + preserved_table_docs  # keep non-targeted tables in the rollup
        cols = sum(int(td.get("column_count") or 0) for td in table_docs)
        self._emit_db(database, f"{database} · {len(table_docs)} tables · {cols} columns", status="completed")

        # Write database-level document (depends on all tables)
        self.store.write_json(
            self.store.database_dir(instance, database) / "tables.json",
            {
                "instance": instance,
                "database": database,
                **self.store.connection_metadata(self.connection),
                "tables": table_docs,
            },
        )
        database_doc = self.summarizer.database_doc(instance=instance, database=database, tables=table_docs)
        database_doc["table_count"] = len(table_docs)
        database_doc.update(self.store.connection_metadata(self.connection))
        database_doc["build_options"] = asdict(options)
        self.store.write_json(self.store.database_dir(instance, database) / "database.json", database_doc)
        return database_doc

    def _build_table(self, instance: str, database: str, table, *, options: BuildOptions,
                     stats: BuildStats) -> dict:
        # Capture every query this table runs (this worker owns the table end-to-end)
        # and emit the table node live so the trace shows it the moment it starts.
        self._tls.bucket = []
        failed = False
        table_key = self._table_key(table)
        self._emit_table(database, table_key, status="running", note="starting…")
        try:
            return self._build_table_inner(instance, database, table, options=options,
                                           stats=stats)
        except Exception:
            failed = True
            raise
        finally:
            self._emit_table(database, table_key, status="failed" if failed else "completed")
            captured = self._tls.bucket
            self._tls.bucket = None
            with self._table_sql_lock:
                self._table_sql[f"{database}.{table_key}"] = captured or []

    def _build_table_inner(self, instance: str, database: str, table, *, options: BuildOptions,
                           stats: BuildStats) -> dict:
        table_key = self._table_key(table)
        doc_table = self._doc_table_info(table)
        self._emit_table(database, table_key, status="running", note="describing…")
        self._bump(stats, tables=1)
        columns = self.adapter.describe_table(table_key, database=database)
        foreign_keys = self.adapter.foreign_keys(table_key, database=database)
        try:
            indexes = self.adapter.indexes(table_key, database=database)
        except Exception:
            indexes = []
        try:
            ddl = self.adapter.get_table_ddl(table_key, database=database)
        except Exception:
            ddl = ""
        if self.join_catalog is not None and foreign_keys:
            fk_rels = [
                {
                    "database": database, "table": fk.table or table_key, "column": fk.column,
                    "ref_table": fk.ref_table, "ref_column": fk.ref_column,
                    "confidence": 0.97, "join_type": "many_to_one", "validated": True,
                    "reason": "declared foreign key",
                }
                for fk in foreign_keys
            ]
            with self._table_sql_lock:
                self._fk_relations.extend(fk_rels)

        # Large tables keep an estimated row-count (no full scan); smaller ones get
        # an exact COUNT(*).
        heavy = self._is_heavy(table, options)
        if not heavy:
            self._bump(stats, light_tables=1)
        if options.sample:
            next_step = "sampling…"
        elif options.collect_row_counts:
            next_step = "counting rows…"
        else:
            next_step = "writing metadata…"
        self._emit_table(database, table_key, status="running", note=next_step)
        row_count = (
            self._table_row_count(table, database=database, heavy=heavy)
            if options.collect_row_counts else None
        )
        sample_rows: list[dict] = []
        if options.sample:
            try:
                sample_rows = self.adapter.sample_rows(
                    table_key, database=database,
                    limit=min(options.sample_limit, max(20, options.top_k)),
                ).rows
            except Exception as exc:
                self._record_error(stats, f"{instance}.{database}.{table_key}.sample: {type(exc).__name__}: {exc}")

        # The table is the disclosure leaf: one structured document with the full
        # DDL-as-JSON, indexes, FKs, row-count and a truncated sample. No per-column
        # documents or profiling — the agent fetches column stats on demand.
        table_doc = self.summarizer.table_doc(
            instance=instance, database=database, table=doc_table,
            columns=columns, foreign_keys=foreign_keys, indexes=indexes, ddl=ddl,
            row_count=row_count, sample_rows=sample_rows,
        )
        table_doc["column_count"] = len(columns)
        table_doc.update(self.store.connection_metadata(self.connection))
        self._bump(stats, columns=len(columns))
        self.store.write_json(self.store.table_dir(instance, database, table_key) / "table.json", table_doc)
        return table_doc

    def _table_row_count(self, table, *, database: str, heavy: bool) -> int | None:
        """Exact COUNT(*) for smaller tables; the catalog estimate for big ones
        (so we never full-scan a large table just to count it). Note: ``heavy`` is
        True for small/scannable tables and False for big ones (see _is_heavy)."""
        if not heavy:  # big table — avoid the full scan
            return table.estimated_rows
        try:
            from dbaide.adapters.base import quote_identifier
            tq = quote_identifier(self._table_key(table), self.adapter.dialect)
            result = self.adapter.execute_readonly(
                f"SELECT COUNT(*) AS n FROM {tq}", database=database, limit=1,
            )
            if result.rows:
                return int(list(result.rows[0].values())[0])
        except Exception:
            pass
        return table.estimated_rows

    def _table_key(self, table: TableInfo) -> str:
        """Storage/execution key for a listed table.

        MySQL stores the selected database in ``TableInfo.schema``; passing
        ``table.ref`` there would break adapter catalog caches. Postgres uses
        ``schema`` as a namespace, so the schema must be part of the table key.
        """
        if self.adapter.dialect == "postgres" and getattr(table, "schema", ""):
            return table.ref
        return table.name

    def _doc_table_info(self, table: TableInfo) -> TableInfo:
        key = self._table_key(table)
        if key == table.name:
            return table
        return TableInfo(
            name=key,
            schema=table.schema,
            comment=table.comment,
            estimated_rows=table.estimated_rows,
            table_type=table.table_type,
        )

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
    return kind_from_type(column) in {"temporal", "boolean", "numeric"}
