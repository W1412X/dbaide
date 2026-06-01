from __future__ import annotations

import logging
import os
import time

from dbaide.adapters.base import DatabaseAdapter, append_limit, quote_identifier, rows_to_result
from dbaide.models import ColumnInfo, ColumnProfile, ForeignKeyInfo, QueryResult, TableInfo

logger = logging.getLogger("dbaide.postgres")


class PostgresAdapter(DatabaseAdapter):
    dialect = "postgres"

    def _connect(self, database: str = ""):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Install PostgreSQL support with `pip install dbaide[postgres]`.") from exc
        password = self.config.password or (os.environ.get(self.config.password_env) if self.config.password_env else "")
        return psycopg.connect(
            host=self.config.host or "localhost",
            port=int(self.config.port or 5432),
            user=self.config.user or None,
            password=password or None,
            dbname=database or self.config.database or "postgres",
            row_factory=dict_row,
        )

    def test(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1").fetchone()

    def list_databases(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname").fetchall()
            return [row["datname"] for row in rows]

    def list_tables(self, database: str = "") -> list[TableInfo]:
        sql = """
        SELECT c.relname AS name, n.nspname AS schema, obj_description(c.oid) AS comment,
               CASE c.relkind WHEN 'v' THEN 'view' ELSE 'table' END AS table_type,
               c.reltuples::bigint AS estimated_rows
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r','v') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY n.nspname, c.relname
        """
        with self._connect(database) as conn:
            return [TableInfo(**row) for row in conn.execute(sql).fetchall()]

    def describe_table(self, table: str, database: str = "") -> list[ColumnInfo]:
        schema, table_name = _split_schema(table)
        sql = """
        SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS data_type,
               NOT a.attnotnull AS nullable, pg_get_expr(d.adbin, d.adrelid) AS default,
               col_description(a.attrelid, a.attnum) AS comment,
               EXISTS (
                 SELECT 1 FROM pg_index i WHERE i.indrelid = a.attrelid AND a.attnum = ANY(i.indkey) AND i.indisprimary
               ) AS primary_key,
               EXISTS (
                 SELECT 1 FROM pg_index i WHERE i.indrelid = a.attrelid AND a.attnum = ANY(i.indkey)
               ) AS indexed
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relname = %s AND (%s = '' OR n.nspname = %s)
        ORDER BY a.attnum
        """
        with self._connect(database if database and "." not in database else "") as conn:
            rows = conn.execute(sql, (table_name, schema, schema)).fetchall()
            return [ColumnInfo(**row) for row in rows]

    def foreign_keys(self, table: str, database: str = "") -> list[ForeignKeyInfo]:
        schema, table_name = _split_schema(table)
        sql = """
        SELECT kcu.table_name AS table, kcu.column_name AS column,
               ccu.table_name AS ref_table, ccu.column_name AS ref_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_name = %s AND (%s = '' OR tc.table_schema = %s)
        """
        with self._connect(database if database and "." not in database else "") as conn:
            return [ForeignKeyInfo(**row) for row in conn.execute(sql, (table_name, schema, schema)).fetchall()]

    def _execute_readonly_impl(self, sql: str, *, database: str = "", limit: int | None = None,
                               timeout_seconds: int = 10) -> QueryResult:
        bounded = append_limit(sql, limit)
        start = time.perf_counter()
        conn = self._connect(database if database and "." not in database else "")
        try:
            # psycopg3 manages the transaction itself, so an explicit "BEGIN READ ONLY"
            # races its implicit BEGIN and may be ignored. Set read-only on the
            # connection before the first statement instead — the transaction it opens
            # is then genuinely read-only.
            conn.read_only = True
            conn.execute("SET statement_timeout = %s", (max(1, int(timeout_seconds * 1000)),))
            rows = conn.execute(bounded).fetchall()
            conn.rollback()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()
        elapsed = (time.perf_counter() - start) * 1000
        logger.debug("execute rows=%d elapsed_ms=%.1f sql=%s", len(rows), elapsed, bounded[:200])
        return rows_to_result([dict(row) for row in rows], sql=bounded, elapsed_ms=elapsed)

    def explain(self, sql: str, *, database: str = "", timeout_seconds: int = 10) -> QueryResult:
        return self.execute_readonly(
            "EXPLAIN " + sql.strip().rstrip(";"),
            database=database, limit=None, timeout_seconds=timeout_seconds,
        )

    def sample_rows(self, table: str, *, database: str = "", limit: int = 20) -> QueryResult:
        return self.execute_readonly(
            f"SELECT * FROM {_quote_table(table)}",
            database=database, limit=limit,
        )

    def profile_column(self, table: str, column: str, *, database: str = "", top_k: int = 10,
                       timeout_seconds: int = 30, heavy_scan: bool = True,
                       include_avg: bool = False, include_length: bool = False) -> ColumnProfile:
        tq, cq = _quote_table(table), quote_identifier(column, self.dialect)
        agg_parts = [
            "COUNT(*) row_count",
            f"COUNT(*) FILTER (WHERE {cq} IS NULL) null_count",
            f"MIN({cq}) min_value",
            f"MAX({cq}) max_value",
        ]
        if heavy_scan:
            agg_parts.insert(2, f"COUNT(DISTINCT {cq}) distinct_count")
        if include_avg:
            agg_parts.append(f"AVG({cq}) avg_value")
        if include_length:
            agg_parts.append(f"MIN(LENGTH({cq}::text)) min_length")
            agg_parts.append(f"MAX(LENGTH({cq}::text)) max_length")
            agg_parts.append(f"AVG(LENGTH({cq}::text)) avg_length")
        sql = f"SELECT {', '.join(agg_parts)} FROM {tq}"
        rows = self.execute_readonly(sql, database=database, limit=None, timeout_seconds=timeout_seconds).rows
        if not rows:
            return ColumnProfile(table=table, column=column, row_count=0, null_count=0)
        row = rows[0]
        top: list = []
        if heavy_scan:
            top_sql = (
                f"SELECT {cq} value, COUNT(*) count FROM {tq} "
                f"WHERE {cq} IS NOT NULL GROUP BY {cq} ORDER BY count DESC LIMIT {int(top_k)}"
            )
            top = self.execute_readonly(top_sql, database=database, limit=None, timeout_seconds=timeout_seconds).rows
        numeric_stats = {"avg": row.get("avg_value")} if include_avg else {}
        text_stats = (
            {"min_length": row.get("min_length"), "max_length": row.get("max_length"), "avg_length": row.get("avg_length")}
            if include_length else {}
        )
        return ColumnProfile(
            table=table, column=column,
            row_count=int(row.get("row_count") or 0),
            null_count=int(row.get("null_count") or 0),
            distinct_count=int(row.get("distinct_count") or 0) if heavy_scan else None,
            min_value=row.get("min_value"),
            max_value=row.get("max_value"),
            top_values=top,
            numeric_stats=numeric_stats,
            text_stats=text_stats,
        )


def _split_schema(table: str) -> tuple[str, str]:
    if "." in table:
        left, right = table.split(".", 1)
        return left, right
    return "", table


def _quote_table(table: str) -> str:
    schema, name = _split_schema(table)
    if schema:
        return f"{quote_identifier(schema, 'postgres')}.{quote_identifier(name, 'postgres')}"
    return quote_identifier(name, "postgres")
