from __future__ import annotations

import logging
import os
import threading
import time

from dbaide.adapters.base import DatabaseAdapter, append_limit, quote_identifier, rows_to_result
from dbaide.db.connection_pool import PoolKey, for_key as connection_pool_for_key
from dbaide.models import ColumnInfo, ColumnProfile, ForeignKeyInfo, IndexInfo, QueryResult, TableInfo

logger = logging.getLogger("dbaide.mysql")


class MySQLAdapter(DatabaseAdapter):
    dialect = "mysql"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._catalog_lock = threading.Lock()
        self._columns_by_db: dict[str, dict[str, list[ColumnInfo]]] = {}
        self._foreign_keys_by_db: dict[str, dict[str, list[ForeignKeyInfo]]] = {}
        self._indexes_by_db: dict[str, dict[str, list[IndexInfo]]] = {}

    @property
    def _is_mariadb(self) -> bool:
        return self.config.type == "mariadb"

    def _open_connection(self, database: str = ""):
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

    def _connect(self, database: str = ""):
        db = database or self.config.database or ""

        def factory():
            return self._open_connection(db)

        def validator(conn) -> bool:
            if not getattr(conn, "open", False):
                return False
            conn.ping(reconnect=False)
            return True

        return connection_pool_for_key(
            PoolKey(self.config.name, self.config.type or "mysql", db),
            max_size=self.policy.max_inflight_queries,
            factory=factory,
            validator=validator,
        ).acquire()

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

    def server_version(self) -> str:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS version")
            row = cur.fetchone() or {}
        return str(row.get("version") or "")

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
        return list(self._load_columns(db).get(table, []))

    def _load_columns(self, db: str) -> dict[str, list[ColumnInfo]]:
        with self._catalog_lock:
            cached = self._columns_by_db.get(db)
            if cached is not None:
                return cached
            sql = """
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_COMMENT, COLUMN_KEY
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """
            grouped: dict[str, list[ColumnInfo]] = {}
            with self._connect(db) as conn, conn.cursor() as cur:
                cur.execute(sql, (db,))
                for row in cur.fetchall():
                    grouped.setdefault(str(row["TABLE_NAME"]), []).append(
                        ColumnInfo(
                            name=row["COLUMN_NAME"],
                            data_type=row.get("DATA_TYPE") or "",
                            nullable=row.get("IS_NULLABLE") == "YES",
                            default=row.get("COLUMN_DEFAULT"),
                            comment=row.get("COLUMN_COMMENT") or "",
                            primary_key=row.get("COLUMN_KEY") == "PRI",
                            indexed=bool(row.get("COLUMN_KEY")),
                        )
                    )
            self._columns_by_db.setdefault(db, grouped)
            return self._columns_by_db[db]

    def foreign_keys(self, table: str, database: str = "") -> list[ForeignKeyInfo]:
        db = database or self.config.database
        if not db:
            raise ValueError("Database is required for MySQL. Specify --database or set database in connection config.")
        return list(self._load_foreign_keys(db).get(table, []))

    def _load_foreign_keys(self, db: str) -> dict[str, list[ForeignKeyInfo]]:
        with self._catalog_lock:
            cached = self._foreign_keys_by_db.get(db)
            if cached is not None:
                return cached
            sql = """
            SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """
            grouped: dict[str, list[ForeignKeyInfo]] = {}
            with self._connect(db) as conn, conn.cursor() as cur:
                cur.execute(sql, (db,))
                for row in cur.fetchall():
                    table = str(row["TABLE_NAME"])
                    grouped.setdefault(table, []).append(
                        ForeignKeyInfo(
                            table=table,
                            column=row["COLUMN_NAME"],
                            ref_table=row["REFERENCED_TABLE_NAME"],
                            ref_column=row["REFERENCED_COLUMN_NAME"],
                        )
                    )
            self._foreign_keys_by_db.setdefault(db, grouped)
            return self._foreign_keys_by_db[db]

    def indexes(self, table: str, database: str = "") -> list[IndexInfo]:
        db = database or self.config.database
        if not db:
            raise ValueError("Database is required for MySQL. Specify --database or set database in connection config.")
        return list(self._load_indexes(db).get(table, []))

    def _load_indexes(self, db: str) -> dict[str, list[IndexInfo]]:
        with self._catalog_lock:
            cached = self._indexes_by_db.get(db)
            if cached is not None:
                return cached
            sql = """
            SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, INDEX_TYPE
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
            """
            grouped: dict[str, dict[str, dict]] = {}
            with self._connect(db) as conn, conn.cursor() as cur:
                cur.execute(sql, (db,))
                for row in cur.fetchall():
                    table = str(row["TABLE_NAME"])
                    name = row["INDEX_NAME"]
                    table_group = grouped.setdefault(table, {})
                    entry = table_group.setdefault(name, {
                        "columns": [], "unique": not bool(row["NON_UNIQUE"]),
                        "type": (row.get("INDEX_TYPE") or "").lower(),
                    })
                    entry["columns"].append(row["COLUMN_NAME"])
            out: dict[str, list[IndexInfo]] = {}
            for table, indexes in grouped.items():
                out[table] = [
                    IndexInfo(name=name, columns=info["columns"], unique=info["unique"],
                              type=info["type"], primary=(name == "PRIMARY"))
                    for name, info in indexes.items()
                ]
            self._indexes_by_db.setdefault(db, out)
            return self._indexes_by_db[db]

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

    def _execute_readonly_impl(self, sql: str, *, database: str = "", limit: int | None = None,
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
                       timeout_seconds: int = 30, heavy_scan: bool = True,
                       include_avg: bool = False, include_length: bool = False) -> ColumnProfile:
        tq, cq = quote_identifier(table, self.dialect), quote_identifier(column, self.dialect)
        agg_parts = [
            "COUNT(*) row_count",
            f"SUM(CASE WHEN {cq} IS NULL THEN 1 ELSE 0 END) null_count",
            f"MIN({cq}) min_value",
            f"MAX({cq}) max_value",
        ]
        if heavy_scan:
            agg_parts.insert(2, f"COUNT(DISTINCT {cq}) distinct_count")
        if include_avg:
            agg_parts.append(f"AVG({cq}) avg_value")
        if include_length:
            agg_parts.append(f"MIN(LENGTH({cq})) min_length")
            agg_parts.append(f"MAX(LENGTH({cq})) max_length")
            agg_parts.append(f"AVG(LENGTH({cq})) avg_length")
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
