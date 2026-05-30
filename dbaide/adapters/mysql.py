from __future__ import annotations

import logging
import os
import time

from dbaide.adapters.base import DatabaseAdapter, append_limit, quote_identifier, rows_to_result
from dbaide.models import ColumnInfo, ColumnProfile, ForeignKeyInfo, QueryResult, TableInfo

logger = logging.getLogger("dbaide.mysql")


class MySQLAdapter(DatabaseAdapter):
    dialect = "mysql"

    @property
    def _is_mariadb(self) -> bool:
        return self.config.type == "mariadb"

    def _connect(self, database: str = ""):
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("Install MySQL support with `pip install dbaide[mysql]`.") from exc
        password = self.config.password or (os.environ.get(self.config.password_env) if self.config.password_env else "")
        db = database or self.config.database or None
        return pymysql.connect(
            host=self.config.host or "localhost",
            port=int(self.config.port or 3306),
            user=self.config.user,
            password=password or "",
            database=db,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def _set_timeout(self, cur, timeout_seconds: int) -> None:
        """Set per-statement timeout using the correct variable for the engine."""
        ms = max(1, int(timeout_seconds * 1000))
        if self._is_mariadb:
            # MariaDB uses max_statement_time in seconds (float)
            cur.execute("SET SESSION max_statement_time = %s", (max(0.001, timeout_seconds),))
        else:
            cur.execute("SET SESSION max_execution_time = %s", (ms,))

    def test(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

    def list_databases(self) -> list[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            all_dbs = [str(next(iter(row.values()))) for row in cur.fetchall()]
            # Filter out system databases
            system_dbs = {"information_schema", "mysql", "performance_schema", "sys"}
            user_dbs = [db for db in all_dbs if db not in system_dbs]
            logger.debug("list_databases: all=%s, user=%s", all_dbs, user_dbs)
            return user_dbs

    def list_tables(self, database: str = "") -> list[TableInfo]:
        db = database or self.config.database
        if not db:
            raise ValueError("Database is required for MySQL. Specify --database or set database in connection config.")
        sql = """
        SELECT TABLE_NAME AS name, TABLE_COMMENT AS comment, TABLE_ROWS AS estimated_rows, TABLE_TYPE AS table_type
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s
        ORDER BY TABLE_NAME
        """
        with self._connect(db) as conn, conn.cursor() as cur:
            cur.execute(sql, (db,))
            return [
                TableInfo(
                    name=row["name"],
                    schema=db,
                    comment=row.get("comment") or "",
                    estimated_rows=row.get("estimated_rows"),
                    table_type=row.get("table_type") or "table",
                )
                for row in cur.fetchall()
            ]

    def describe_table(self, table: str, database: str = "") -> list[ColumnInfo]:
        db = database or self.config.database
        if not db:
            raise ValueError("Database is required for MySQL. Specify --database or set database in connection config.")
        sql = """
        SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_COMMENT, COLUMN_KEY
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
        """
        with self._connect(db) as conn, conn.cursor() as cur:
            cur.execute(sql, (db, table))
            return [
                ColumnInfo(
                    name=row["COLUMN_NAME"],
                    data_type=row.get("DATA_TYPE") or "",
                    nullable=row.get("IS_NULLABLE") == "YES",
                    default=row.get("COLUMN_DEFAULT"),
                    comment=row.get("COLUMN_COMMENT") or "",
                    primary_key=row.get("COLUMN_KEY") == "PRI",
                    indexed=bool(row.get("COLUMN_KEY")),
                )
                for row in cur.fetchall()
            ]

    def foreign_keys(self, table: str, database: str = "") -> list[ForeignKeyInfo]:
        db = database or self.config.database
        if not db:
            raise ValueError("Database is required for MySQL. Specify --database or set database in connection config.")
        sql = """
        SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL
        """
        with self._connect(db) as conn, conn.cursor() as cur:
            cur.execute(sql, (db, table))
            return [
                ForeignKeyInfo(
                    table=table,
                    column=row["COLUMN_NAME"],
                    ref_table=row["REFERENCED_TABLE_NAME"],
                    ref_column=row["REFERENCED_COLUMN_NAME"],
                )
                for row in cur.fetchall()
            ]

    def get_table_ddl(self, table: str, database: str = "") -> str:
        db = database or self.config.database
        if not db:
            raise ValueError("Database is required for MySQL. Specify --database or set database in connection config.")
        with self._connect(db) as conn, conn.cursor() as cur:
            cur.execute(f"SHOW CREATE TABLE {quote_identifier(table, self.dialect)}")
            row = cur.fetchone() or {}
        for key, value in row.items():
            if "Create" in str(key):
                return str(value)
        return super().get_table_ddl(table, database=database)

    def execute_readonly(self, sql: str, *, database: str = "", limit: int | None = None,
                         timeout_seconds: int = 10) -> QueryResult:
        bounded = append_limit(sql, limit)
        start = time.perf_counter()
        conn = self._connect(database)
        try:
            with conn.cursor() as cur:
                cur.execute("START TRANSACTION READ ONLY")
                self._set_timeout(cur, timeout_seconds)
                cur.execute(bounded)
                rows = list(cur.fetchall())
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
        return rows_to_result(rows, sql=bounded, elapsed_ms=elapsed)

    def explain(self, sql: str, *, database: str = "", timeout_seconds: int = 10) -> QueryResult:
        return self.execute_readonly(
            "EXPLAIN " + sql.strip().rstrip(";"),
            database=database, limit=None, timeout_seconds=timeout_seconds,
        )

    def sample_rows(self, table: str, *, database: str = "", limit: int = 20) -> QueryResult:
        return self.execute_readonly(
            f"SELECT * FROM {quote_identifier(table, self.dialect)}",
            database=database, limit=limit,
        )

    def profile_column(self, table: str, column: str, *, database: str = "", top_k: int = 10,
                       timeout_seconds: int = 30) -> ColumnProfile:
        tq, cq = quote_identifier(table, self.dialect), quote_identifier(column, self.dialect)
        sql = (
            f"SELECT COUNT(*) row_count, "
            f"SUM(CASE WHEN {cq} IS NULL THEN 1 ELSE 0 END) null_count, "
            f"COUNT(DISTINCT {cq}) distinct_count, "
            f"MIN({cq}) min_value, MAX({cq}) max_value "
            f"FROM {tq}"
        )
        rows = self.execute_readonly(sql, database=database, limit=None, timeout_seconds=timeout_seconds).rows
        if not rows:
            return ColumnProfile(table=table, column=column, row_count=0, null_count=0)
        row = rows[0]
        top_sql = (
            f"SELECT {cq} value, COUNT(*) count FROM {tq} "
            f"WHERE {cq} IS NOT NULL GROUP BY {cq} ORDER BY count DESC LIMIT {int(top_k)}"
        )
        top = self.execute_readonly(top_sql, database=database, limit=None, timeout_seconds=timeout_seconds).rows
        return ColumnProfile(
            table=table, column=column,
            row_count=int(row.get("row_count") or 0),
            null_count=int(row.get("null_count") or 0),
            distinct_count=int(row.get("distinct_count") or 0),
            min_value=row.get("min_value"),
            max_value=row.get("max_value"),
            top_values=top,
        )
