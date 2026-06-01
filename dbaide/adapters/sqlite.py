from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from dbaide.adapters.base import DatabaseAdapter, append_limit, quote_identifier, rows_to_result
from dbaide.models import ColumnInfo, ColumnProfile, ForeignKeyInfo, IndexInfo, QueryResult, TableInfo

logger = logging.getLogger("dbaide.sqlite")


class _TimeoutError(TimeoutError):
    """Raised when SQLite progress handler signals a timeout."""


class SQLiteAdapter(DatabaseAdapter):
    dialect = "sqlite"

    @property
    def path(self) -> str:
        if not self.config.path:
            raise ValueError("SQLite connection requires path")
        return str(Path(self.config.path).expanduser())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _guarded_conn(self, timeout_seconds: int = 10, *, readonly: bool = True) -> Iterator[sqlite3.Connection]:
        """Yield a connection with read-only mode and a progress-handler timeout.

        The progress handler fires every ~5000 VM instructions.  If the wall-
        clock deadline has passed, it returns 1 which causes SQLite to abort
        the current statement with an ``sqlite3.OperationalError``.
        """
        deadline = time.perf_counter() + max(1, timeout_seconds)

        def _timeout_handler() -> int:
            if time.perf_counter() >= deadline:
                logger.warning("sqlite timeout after %ds", timeout_seconds)
                return 1
            return 0

        conn = self._connect()
        try:
            if readonly:
                conn.execute("PRAGMA query_only = ON")
            conn.set_progress_handler(_timeout_handler, 5000)
            yield conn
        finally:
            conn.set_progress_handler(None, 0)
            conn.close()

    def test(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1").fetchone()

    def list_databases(self) -> list[str]:
        return [self.config.database or "main"]

    def list_tables(self, database: str = "") -> list[TableInfo]:
        sql = """
        SELECT name, type
        FROM sqlite_master
        WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
            out: list[TableInfo] = []
            for row in rows:
                count = self._estimate_rows_fast(conn, row["name"]) if row["type"] == "table" else None
                out.append(TableInfo(name=row["name"], estimated_rows=count, table_type=row["type"]))
            return out

    def describe_table(self, table: str, database: str = "") -> list[ColumnInfo]:
        with self._connect() as conn:
            pragma_rows = conn.execute(f"PRAGMA table_info({quote_identifier(table, self.dialect)})").fetchall()
            index_cols = self._indexed_columns(conn, table)
            return [
                ColumnInfo(
                    name=row["name"],
                    data_type=row["type"] or "",
                    nullable=not bool(row["notnull"]),
                    default=None if row["dflt_value"] is None else str(row["dflt_value"]),
                    primary_key=bool(row["pk"]),
                    indexed=row["name"] in index_cols,
                )
                for row in pragma_rows
            ]

    def foreign_keys(self, table: str, database: str = "") -> list[ForeignKeyInfo]:
        with self._connect() as conn:
            rows = conn.execute(f"PRAGMA foreign_key_list({quote_identifier(table, self.dialect)})").fetchall()
            return [
                ForeignKeyInfo(table=table, column=row["from"], ref_table=row["table"], ref_column=row["to"])
                for row in rows
            ]

    def indexes(self, table: str, database: str = "") -> list[IndexInfo]:
        out: list[IndexInfo] = []
        with self._connect() as conn:
            try:
                idx_list = conn.execute(
                    f"PRAGMA index_list({quote_identifier(table, self.dialect)})"
                ).fetchall()
            except sqlite3.Error:
                return out
            for idx in idx_list:
                name = str(idx["name"])
                cols = [
                    str(r["name"])
                    for r in conn.execute(
                        f"PRAGMA index_info({quote_identifier(name, self.dialect)})"
                    ).fetchall()
                ]
                # origin: 'pk' (primary key), 'u' (unique constraint), 'c' (CREATE INDEX)
                origin = str(idx["origin"]) if "origin" in idx.keys() else "c"
                out.append(IndexInfo(
                    name=name, columns=cols,
                    unique=bool(idx["unique"]), type="btree", primary=(origin == "pk"),
                ))
        return out

    def get_table_ddl(self, table: str, database: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
                (table,),
            ).fetchone()
        if row and row["sql"]:
            return str(row["sql"])
        return super().get_table_ddl(table, database=database)

    def _execute_readonly_impl(self, sql: str, *, database: str = "", limit: int | None = None, timeout_seconds: int = 10) -> QueryResult:
        bounded_sql = append_limit(sql, limit)
        start = time.perf_counter()
        with self._guarded_conn(timeout_seconds) as conn:
            rows = [dict(row) for row in conn.execute(bounded_sql).fetchall()]
        elapsed = (time.perf_counter() - start) * 1000
        logger.debug("execute rows=%d elapsed_ms=%.1f sql=%s", len(rows), elapsed, bounded_sql[:200])
        return rows_to_result(rows, sql=bounded_sql, elapsed_ms=elapsed)

    def explain(self, sql: str, *, database: str = "", timeout_seconds: int = 10) -> QueryResult:
        explain_sql = "EXPLAIN QUERY PLAN " + sql.strip().rstrip(";")
        start = time.perf_counter()
        with self._record_query(explain_sql, database=database):
            with self._guarded_conn(timeout_seconds) as conn:
                rows = [dict(row) for row in conn.execute(explain_sql).fetchall()]
        elapsed = (time.perf_counter() - start) * 1000
        logger.debug("explain elapsed_ms=%.1f sql=%s", elapsed, explain_sql[:200])
        return rows_to_result(rows, sql=explain_sql, elapsed_ms=elapsed)

    def sample_rows(self, table: str, *, database: str = "", limit: int = 20) -> QueryResult:
        table_q = quote_identifier(table, self.dialect)
        return self.execute_readonly(f"SELECT * FROM {table_q}", limit=limit)

    def profile_column(self, table: str, column: str, *, database: str = "", top_k: int = 10,
                       timeout_seconds: int = 30, heavy_scan: bool = True,
                       include_avg: bool = False, include_length: bool = False) -> ColumnProfile:
        tq = quote_identifier(table, self.dialect)
        cq = quote_identifier(column, self.dialect)
        # Build a single aggregate scan; fold in avg/length so we never scan twice.
        agg_parts = [
            "COUNT(*) AS row_count",
            f"SUM(CASE WHEN {cq} IS NULL THEN 1 ELSE 0 END) AS null_count",
            f"MIN({cq}) AS min_value",
            f"MAX({cq}) AS max_value",
        ]
        if heavy_scan:
            agg_parts.insert(2, f"COUNT(DISTINCT {cq}) AS distinct_count")
        if include_avg:
            agg_parts.append(f"AVG({cq}) AS avg_value")
        if include_length:
            agg_parts.append(f"MIN(LENGTH({cq})) AS min_length")
            agg_parts.append(f"MAX(LENGTH({cq})) AS max_length")
            agg_parts.append(f"AVG(LENGTH({cq})) AS avg_length")
        agg_sql = f"SELECT {', '.join(agg_parts)} FROM {tq}"
        top_sql = (
            f"SELECT {cq} AS value, COUNT(*) AS count FROM {tq} "
            f"WHERE {cq} IS NOT NULL GROUP BY {cq} ORDER BY count DESC LIMIT ?"
        )
        sample_sql = f"SELECT DISTINCT {cq} AS value FROM {tq} WHERE {cq} IS NOT NULL LIMIT ?"
        start = time.perf_counter()
        with self._record_query(agg_sql, database=database):
            with self._guarded_conn(timeout_seconds) as conn:
                row = conn.execute(agg_sql).fetchone()
                top = conn.execute(top_sql, (top_k,)).fetchall() if heavy_scan else []
                sample = conn.execute(sample_sql, (top_k,)).fetchall()
        elapsed = (time.perf_counter() - start) * 1000
        logger.debug("profile_column %s.%s elapsed_ms=%.1f heavy=%s", table, column, elapsed, heavy_scan)
        keys = row.keys() if row is not None else []
        numeric_stats = {"avg": row["avg_value"]} if include_avg and "avg_value" in keys else {}
        text_stats = (
            {"min_length": row["min_length"], "max_length": row["max_length"], "avg_length": row["avg_length"]}
            if include_length and "avg_length" in keys else {}
        )
        return ColumnProfile(
            table=table,
            column=column,
            row_count=int(row["row_count"] or 0),
            null_count=int(row["null_count"] or 0),
            distinct_count=int(row["distinct_count"]) if heavy_scan and row["distinct_count"] is not None else None,
            min_value=row["min_value"],
            max_value=row["max_value"],
            top_values=[{"value": item["value"], "count": item["count"]} for item in top],
            sample_values=[item["value"] for item in sample],
            numeric_stats=numeric_stats,
            text_stats=text_stats,
        )

    def _estimate_rows_fast(self, conn: sqlite3.Connection, table: str) -> int | None:
        """Estimate row count without a full table scan.

        Uses ``sqlite_stat1`` (populated by ``ANALYZE``); falls back to the
        page-level ``dbstat`` virtual table when available. Returns ``None`` if
        neither is available rather than blocking on COUNT(*).
        """
        # sqlite_stat1: "NNN ..."  where NNN is the row count estimate
        try:
            row = conn.execute(
                "SELECT stat FROM sqlite_stat1 WHERE tbl = ? AND idx IS NULL",
                (table,),
            ).fetchone()
            if row and row["stat"]:
                return int(row["stat"].split()[0])
        except (sqlite3.Error, ValueError):
            pass
        # dbstat: sum of leaf-cell counts for the table (columns are name/pageno/ncell;
        # there is no 'pgno'/'aggregate' column — the aggregate form is dbstat(schema,1)).
        try:
            row = conn.execute(
                "SELECT SUM(ncell) AS cnt FROM dbstat WHERE name = ? AND pagetype = 'leaf'",
                (table,),
            ).fetchone()
            if row and row["cnt"] is not None:
                return int(row["cnt"])
        except (sqlite3.Error, ValueError):
            pass
        return None

    def _indexed_columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        cols: set[str] = set()
        try:
            for idx in conn.execute(f"PRAGMA index_list({quote_identifier(table, self.dialect)})").fetchall():
                for row in conn.execute(f"PRAGMA index_info({quote_identifier(idx['name'], self.dialect)})").fetchall():
                    cols.add(str(row["name"]))
        except sqlite3.Error:
            pass
        return cols
