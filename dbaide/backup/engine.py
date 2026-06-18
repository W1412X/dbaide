"""Backup engine — orchestrates paginated data export from database to local files."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dbaide.adapters import DatabaseAdapter, build_adapter
from dbaide.backup.registry import BackupRegistry
from dbaide.backup.writers import FORMATS
from dbaide.models import ConnectionConfig

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int | None], None]


class BackupEngine:
    def __init__(self, config: ConnectionConfig, registry: BackupRegistry | None = None) -> None:
        self._config = config
        self._registry = registry or BackupRegistry()

    @property
    def registry(self) -> BackupRegistry:
        return self._registry

    def backup_table(
        self,
        database: str,
        table: str,
        *,
        fmt: str = "csv",
        batch_size: int = 5000,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        adapter = build_adapter(self._config, caller="backup")
        return self._do_backup_table(adapter, database, table, fmt=fmt,
                                     batch_size=batch_size, on_progress=on_progress)

    def backup_database(
        self,
        database: str,
        *,
        fmt: str = "csv",
        batch_size: int = 5000,
        threads: int = 4,
        on_progress: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        adapter = build_adapter(self._config, caller="backup")
        tables = adapter.list_tables(database=database)
        table_names = [t.name for t in tables if t.table_type == "table"]
        if not table_names:
            return []
        return self._parallel_backup(database, table_names, fmt=fmt,
                                     batch_size=batch_size, threads=threads,
                                     on_progress=on_progress, scope="database")

    def backup_instance(
        self,
        *,
        fmt: str = "csv",
        batch_size: int = 5000,
        threads: int = 4,
        on_progress: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        adapter = build_adapter(self._config, caller="backup")
        databases = adapter.list_databases()
        results: list[dict[str, Any]] = []
        for db in databases:
            tables = adapter.list_tables(database=db)
            table_names = [t.name for t in tables if t.table_type == "table"]
            if not table_names:
                continue
            batch = self._parallel_backup(db, table_names, fmt=fmt,
                                          batch_size=batch_size, threads=threads,
                                          on_progress=on_progress, scope="instance")
            results.extend(batch)
        return results

    def _parallel_backup(
        self,
        database: str,
        table_names: list[str],
        *,
        fmt: str,
        batch_size: int,
        threads: int,
        on_progress: ProgressCallback | None,
        scope: str,
    ) -> list[dict[str, Any]]:
        if len(table_names) == 1:
            r = self._do_backup_table(
                build_adapter(self._config, caller="backup"),
                database, table_names[0], fmt=fmt, batch_size=batch_size,
                on_progress=on_progress, scope=scope,
            )
            return [r]

        results: list[dict[str, Any]] = []
        effective_threads = min(threads, len(table_names))

        def _worker(tbl: str) -> dict[str, Any]:
            adapter = build_adapter(self._config, caller="backup")
            return self._do_backup_table(
                adapter, database, tbl, fmt=fmt, batch_size=batch_size,
                on_progress=on_progress, scope=scope,
            )

        with ThreadPoolExecutor(max_workers=effective_threads) as pool:
            futures = {}
            for tbl in table_names:
                fut = pool.submit(_worker, tbl)
                futures[fut] = tbl

            for fut in as_completed(futures):
                tbl = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as exc:
                    logger.warning("backup failed for %s.%s: %s", database, tbl, exc)
                    results.append({"table": tbl, "database": database, "error": str(exc)})
        return results

    def _do_backup_table(
        self,
        adapter: DatabaseAdapter,
        database: str,
        table: str,
        *,
        fmt: str = "csv",
        batch_size: int = 5000,
        on_progress: ProgressCallback | None = None,
        scope: str = "table",
    ) -> dict[str, Any]:
        writer_cls = FORMATS.get(fmt)
        if writer_cls is None:
            raise ValueError(f"Unsupported format: {fmt!r}. Available: {', '.join(FORMATS)}")

        columns_info = adapter.describe_table(table, database=database)
        col_names = [c.name for c in columns_info]
        if not col_names:
            raise ValueError(f"Table {table} has no columns")

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = self._registry.backup_dir(self._config.name, database, table)
        filename = f"{table}_{ts}{writer_cls.suffix}"
        out_path = out_dir / filename

        ddl = ""
        if fmt == "sql":
            try:
                ddl = adapter.get_table_ddl(table, database=database)
            except Exception:
                ddl = ""

        extra_kwargs: dict[str, Any] = {}
        if fmt == "sql":
            extra_kwargs["table_name"] = table
            extra_kwargs["dialect"] = adapter.dialect
            extra_kwargs["ddl"] = ddl
        elif fmt == "sqlite":
            extra_kwargs["table_name"] = table

        writer = writer_cls(out_path, col_names, columns_info, **extra_kwargs)

        total_estimate = adapter.explain_estimated_rows(
            f"SELECT * FROM {_quote_table(table, adapter.dialect)}", database=database
        )

        offset = 0
        total_rows = 0
        while True:
            sql = f"SELECT * FROM {_quote_table(table, adapter.dialect)} LIMIT {batch_size} OFFSET {offset}"
            result = adapter.execute_readonly(sql, database=database, caller="backup")
            rows = result.rows or []
            if not rows:
                break
            writer.write_rows(rows)
            total_rows += len(rows)
            if on_progress:
                on_progress(table, total_rows, total_estimate)
            if len(rows) < batch_size:
                break
            offset += batch_size

        writer.close()
        file_size = writer.file_size

        backup_id = self._registry.record(
            connection=self._config.name,
            database=database,
            table=table,
            fmt=fmt,
            row_count=total_rows,
            file_size=file_size,
            file_path=str(out_path),
            scope=scope,
        )

        return {
            "id": backup_id,
            "connection": self._config.name,
            "database": database,
            "table": table,
            "format": fmt,
            "row_count": total_rows,
            "file_size": file_size,
            "file_path": str(out_path),
        }


def _quote_table(table: str, dialect: str) -> str:
    from dbaide.adapters.base import quote_identifier
    return quote_identifier(table, dialect)
