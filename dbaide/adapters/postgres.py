from __future__ import annotations

import logging
import os
import time

from dbaide.adapters.base import DatabaseAdapter, append_limit, dedupe_columns, quote_identifier, rows_to_result
from dbaide.db.connection_pool import PoolKey, for_key as connection_pool_for_key
from dbaide.models import ColumnInfo, ColumnProfile, ForeignKeyInfo, IndexInfo, QueryResult, TableInfo

logger = logging.getLogger("dbaide.postgres")


class PostgresAdapter(DatabaseAdapter):
    dialect = "postgres"

    def _open_connection(self, database: str = ""):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Install PostgreSQL support with `pip install dbaide[postgres]`.") from exc
        password = self.config.password or (os.environ.get(self.config.password_env) if self.config.password_env else "")
        conn_kwargs: dict = dict(
            host=self.config.host or "localhost",
            port=int(self.config.port or 5432),
            user=self.config.user or None,
            password=password or None,
            dbname=database or self.config.database or "postgres",
            row_factory=dict_row,
        )
        # TLS: libpq understands sslmode natively (disable/allow/prefer/require/
        # verify-ca/verify-full). For the verify-* modes, point it at the CA bundle
        # (the user's ssl_ca, else certifi's trust store for public CAs).
        sslmode = getattr(self.config, "sslmode", "") or ""
        if sslmode:
            conn_kwargs["sslmode"] = sslmode
            ca = getattr(self.config, "ssl_ca", "") or ""
            if not ca and sslmode in ("verify-ca", "verify-full"):
                try:
                    import certifi
                    ca = certifi.where()
                except Exception:
                    ca = ""
            if ca:
                conn_kwargs["sslrootcert"] = ca
        conn = psycopg.connect(**conn_kwargs)
        self._set_session_timezone(conn)
        return conn

    def _session_timezone(self) -> str:
        return str(getattr(self.config, "session_timezone", "") or "UTC").strip() or "UTC"

    def _set_session_timezone(self, conn) -> None:
        conn.execute("SELECT set_config('TimeZone', %s, false)", (self._session_timezone(),))
        try:
            conn.commit()
        except Exception:
            pass

    def _connect(self, database: str = ""):
        db = database or self.config.database or "postgres"

        def factory():
            return self._open_connection(db)

        def validator(conn) -> bool:
            return not bool(getattr(conn, "closed", False))

        return connection_pool_for_key(
            PoolKey(self.config.name, self.config.type or "postgres", db, self._session_timezone()),
            max_size=self.policy.max_inflight_queries,
            factory=factory,
            validator=validator,
        ).acquire()

    def test(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1").fetchone()

    def server_version(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SHOW server_version").fetchone() or {}
        return str(row.get("server_version") or "")

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
        SELECT
               CASE WHEN kcu.table_schema <> '' THEN kcu.table_schema || '.' || kcu.table_name ELSE kcu.table_name END AS table,
               kcu.column_name AS column,
               CASE WHEN ccu.table_schema <> '' THEN ccu.table_schema || '.' || ccu.table_name ELSE ccu.table_name END AS ref_table,
               ccu.column_name AS ref_column
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

    def indexes(self, table: str, database: str = "") -> list[IndexInfo]:
        schema, table_name = _split_schema(table)
        sql = """
        SELECT i.relname AS name, ix.indisunique AS unique, ix.indisprimary AS primary,
               am.amname AS type, a.attname AS column, k.ord AS seq
        FROM pg_class t
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_index ix ON ix.indrelid = t.oid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_am am ON am.oid = i.relam
        JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        WHERE t.relname = %s AND (%s = '' OR n.nspname = %s)
        ORDER BY name, seq
        """
        grouped: dict[str, dict] = {}
        with self._connect(database if database and "." not in database else "") as conn:
            for row in conn.execute(sql, (table_name, schema, schema)).fetchall():
                row = dict(row)
                entry = grouped.setdefault(row["name"], {
                    "columns": [], "unique": bool(row["unique"]),
                    "primary": bool(row["primary"]), "type": (row.get("type") or "").lower(),
                })
                entry["columns"].append(row["column"])
        return [
            IndexInfo(name=name, columns=info["columns"], unique=info["unique"],
                      type=info["type"], primary=info["primary"])
            for name, info in grouped.items()
        ]

    def get_table_ddl(self, table: str, database: str = "") -> str:
        """Postgres has no SHOW CREATE TABLE, so reconstruct a faithful CREATE TABLE
        from the catalog: columns (type, NOT NULL, DEFAULT), the primary key, foreign
        keys, and the non-PK indexes as CREATE INDEX statements."""
        cols = self.describe_table(table, database=database)
        if not cols:
            return super().get_table_ddl(table, database=database)
        q = lambda n: quote_identifier(str(n), self.dialect)  # noqa: E731
        tq = _quote_table(table)
        lines: list[str] = []
        for c in cols:
            seg = f"  {q(c.name)} {c.data_type or 'text'}"
            if c.nullable is False:
                seg += " NOT NULL"
            if c.default is not None:
                seg += f" DEFAULT {c.default}"
            lines.append(seg)
        pk_cols = [c.name for c in cols if c.primary_key]
        if pk_cols:
            lines.append(f"  PRIMARY KEY ({', '.join(q(c) for c in pk_cols)})")
        for fk in self.foreign_keys(table, database=database):
            if fk.column and fk.ref_table and fk.ref_column:
                lines.append(
                    f"  FOREIGN KEY ({q(fk.column)}) REFERENCES {q(fk.ref_table)} ({q(fk.ref_column)})"
                )
        ddl = f"CREATE TABLE {tq} (\n" + ",\n".join(lines) + "\n);"
        for ix in self.indexes(table, database=database):
            if ix.primary or not ix.columns:
                continue  # the PK is already inline above
            unique = "UNIQUE " if ix.unique else ""
            ddl += f"\nCREATE {unique}INDEX {q(ix.name)} ON {tq} ({', '.join(q(c) for c in ix.columns)});"
        return ddl

    def _is_connection_error(self, exc: BaseException) -> bool:
        # Prefer psycopg's exception types over message matching (robust to localized
        # server messages). A dropped/broken connection is OperationalError/InterfaceError;
        # a statement_timeout is QueryCanceled (an OperationalError subclass) — exclude it so
        # execute_readonly does not retry and burn another timeout window.
        try:
            import psycopg
            if isinstance(exc, psycopg.errors.QueryCanceled):
                return False
            if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
                return True
        except Exception:
            pass
        return super()._is_connection_error(exc)

    def _execute_readonly_impl(self, sql: str, *, database: str = "", limit: int | None = None,
                               timeout_seconds: int = 10) -> QueryResult:
        bounded = append_limit(sql, limit, dialect=self.dialect)
        start = time.perf_counter()
        conn = self._connect(database if database and "." not in database else "")
        try:
            # psycopg3 manages the transaction itself, so an explicit "BEGIN READ ONLY"
            # races its implicit BEGIN and may be ignored. Set read-only on the
            # connection before the first statement instead — the transaction it opens
            # is then genuinely read-only.
            conn.read_only = True
            conn.execute("SET statement_timeout = %s", (max(1, int(timeout_seconds * 1000)),))
            # Tuple rows + deduped column names so duplicate result columns (e.g. a.id
            # and b.id from a join) aren't collapsed by the connection's dict_row factory.
            from psycopg.rows import tuple_row
            cur = conn.cursor(row_factory=tuple_row)
            cur.execute(bounded)
            cols = dedupe_columns([d.name for d in cur.description]) if cur.description else []
            data = [dict(zip(cols, row)) for row in cur.fetchall()]
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
        logger.debug("execute rows=%d elapsed_ms=%.1f sql=%s", len(data), elapsed, bounded[:200])
        return rows_to_result(data, sql=bounded, elapsed_ms=elapsed, columns=cols)

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
