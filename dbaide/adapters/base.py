from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Iterator

from dbaide.models import ColumnInfo, ColumnProfile, ConnectionConfig, ForeignKeyInfo, IndexInfo, QueryResult, TableInfo


class DatabaseAdapter(ABC):
    dialect = "generic"

    def __init__(self, config: ConnectionConfig) -> None:
        self.config = config
        self.caller = "agent"
        self._policy = None
        self._budget = None
        self._query_log = None

    # ── Resource wiring (lazy defaults keep direct construction working) ──────

    @property
    def policy(self):
        if self._policy is None:
            from dbaide.db.policy import ResourcePolicy
            self._policy = ResourcePolicy.for_load_profile(getattr(self.config, "load_profile", "production"))
        return self._policy

    @policy.setter
    def policy(self, value) -> None:
        self._policy = value
        self._budget = None  # force budget rebuild against the new limit

    @property
    def budget(self):
        if self._budget is None:
            from dbaide.db import budget as budget_registry
            self._budget = budget_registry.for_instance(
                self.config.name, max_inflight=self.policy.max_inflight_queries
            )
        return self._budget

    @property
    def query_log(self):
        if self._query_log is None:
            from dbaide.observability import query_log
            self._query_log = query_log.for_instance(self.config.name)
        return self._query_log

    def attach_resources(self, *, policy=None, caller: str | None = None) -> "DatabaseAdapter":
        if policy is not None:
            self.policy = policy
        if caller is not None:
            self.caller = caller
        return self

    def server_version(self) -> str:
        """Best-effort database server version for dialect-specific SQL guidance."""
        return ""

    # ── Abstract surface ──────────────────────────────────────────────────────

    @abstractmethod
    def test(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_databases(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def list_tables(self, database: str = "") -> list[TableInfo]:
        raise NotImplementedError

    @abstractmethod
    def describe_table(self, table: str, database: str = "") -> list[ColumnInfo]:
        raise NotImplementedError

    def foreign_keys(self, table: str, database: str = "") -> list[ForeignKeyInfo]:
        return []

    def indexes(self, table: str, database: str = "") -> list[IndexInfo]:
        """Index definitions for a table (name, column list, uniqueness, type).
        Default: none. Concrete adapters override with catalog queries."""
        return []

    def get_table_ddl(self, table: str, database: str = "") -> str:
        columns = self.describe_table(table, database=database)
        lines = []
        for column in columns:
            line = f"  {quote_identifier(column.name, self.dialect)} {column.data_type or 'TEXT'}"
            if column.primary_key:
                line += " PRIMARY KEY"
            if column.nullable is False:
                line += " NOT NULL"
            if column.default is not None:
                line += f" DEFAULT {column.default}"
            lines.append(line)
        return f"CREATE TABLE {quote_identifier(table, self.dialect)} (\n" + ",\n".join(lines) + "\n);"

    # ── Guarded read path: every SQL statement goes through here ──────────────

    def execute_readonly(self, sql: str, *, database: str = "", limit: int | None = None,
                         timeout_seconds: int | None = None, caller: str | None = None) -> QueryResult:
        """Template method: acquire budget → apply policy timeout → run → audit.

        Subclasses implement :meth:`_execute_readonly_impl`; they must not be called
        directly so that the concurrency budget and query log always apply.
        """
        effective_timeout = self.policy.statement_timeout_seconds if timeout_seconds is None else timeout_seconds
        who = caller or self.caller
        start = time.perf_counter()
        status, error, result = "ok", "", None
        with self.budget.acquire(who):
            try:
                try:
                    result = self._execute_readonly_impl(sql, database=database, limit=limit,
                                                          timeout_seconds=effective_timeout)
                except Exception as exc:  # noqa: BLE001
                    # A pooled connection the server dropped while idle (idle timeout, pgbouncer,
                    # failover, firewall) fails on first use — the client didn't know it was dead.
                    # The bad connection is discarded on release; retry ONCE with a fresh one.
                    # Safe because these queries are read-only (no side effects). NOT retried for
                    # real query errors (syntax/permission) or statement timeouts.
                    if not self._is_connection_error(exc):
                        raise
                    result = self._execute_readonly_impl(sql, database=database, limit=limit,
                                                          timeout_seconds=effective_timeout)
                return result
            except Exception as exc:  # noqa: BLE001 - record then re-raise
                status, error = "error", f"{type(exc).__name__}: {exc}"
                raise
            finally:
                elapsed = (time.perf_counter() - start) * 1000
                self.query_log.record(
                    caller=who, database=database,
                    sql=result.sql if result is not None else sql,
                    elapsed_ms=elapsed,
                    row_count=result.row_count if result is not None else 0,
                    status=status, error=error,
                )

    @abstractmethod
    def _execute_readonly_impl(self, sql: str, *, database: str = "", limit: int | None = None,
                               timeout_seconds: int = 10) -> QueryResult:
        raise NotImplementedError

    def _is_connection_error(self, exc: BaseException) -> bool:
        """Does this failure look like a dropped/broken connection (so a read-only query is
        safe to retry once with a fresh connection)? Deliberately excludes statement timeouts
        and query cancellations — those are also driver "operational" errors, but retrying one
        re-runs the slow query and waits out another timeout. Adapters may override to add
        driver-specific exception-type checks; the base heuristic matches on the message so it
        works across psycopg / pymysql without importing either."""
        msg = str(exc).lower()
        # Never retry a timeout / cancellation — retrying just burns another timeout window.
        if any(k in msg for k in (
            "timeout", "canceling statement", "cancelling statement",
            "max_execution_time", "max_statement_time", "statement execution time",
            "query execution was interrupted",
        )):
            return False
        return any(k in msg for k in (
            "server closed the connection", "connection is closed", "connection already closed",
            "connection not open", "the connection is closed", "server has gone away",
            "gone away", "lost connection to", "no connection to the server",
            "broken pipe", "eof detected", "ssl connection has been closed",
            "terminating connection due to", "connection reset by peer",
        ))

    @contextmanager
    def _record_query(self, sql: str, *, database: str = "", caller: str | None = None) -> Iterator[None]:
        """Budget + audit wrapper for adapters that run SQL outside execute_readonly
        (e.g. sqlite's multi-statement profile path on a single connection)."""
        who = caller or self.caller
        start = time.perf_counter()
        status, error = "ok", ""
        with self.budget.acquire(who):
            try:
                yield
            except Exception as exc:  # noqa: BLE001
                status, error = "error", f"{type(exc).__name__}: {exc}"
                raise
            finally:
                elapsed = (time.perf_counter() - start) * 1000
                self.query_log.record(caller=who, database=database, sql=sql,
                                      elapsed_ms=elapsed, status=status, error=error)

    @abstractmethod
    def explain(self, sql: str, *, database: str = "", timeout_seconds: int = 10) -> QueryResult:
        raise NotImplementedError

    def explain_estimated_rows(self, sql: str, *, database: str = "") -> int | None:
        """Best-effort row estimate from EXPLAIN. Returns None when not parseable.

        Used as a pre-execution cost gate. SQLite's EXPLAIN QUERY PLAN gives no
        row estimate, so it returns None (no gate possible there).
        """
        try:
            result = self.explain(sql, database=database)
        except Exception:
            return None
        return _parse_explain_rows(self.dialect, result)

    @abstractmethod
    def sample_rows(self, table: str, *, database: str = "", limit: int = 20) -> QueryResult:
        raise NotImplementedError

    @abstractmethod
    def profile_column(self, table: str, column: str, *, database: str = "", top_k: int = 10,
                       timeout_seconds: int = 30, heavy_scan: bool = True,
                       include_avg: bool = False, include_length: bool = False) -> ColumnProfile:
        raise NotImplementedError


def _parse_explain_rows(dialect: str, result: QueryResult) -> int | None:
    rows = result.rows or []
    if not rows:
        return None
    if dialect == "mysql":
        best = None
        for row in rows:
            value = row.get("rows") if isinstance(row, dict) else None
            if value is None and isinstance(row, dict):
                # case-insensitive lookup
                for k, v in row.items():
                    if str(k).lower() == "rows":
                        value = v
                        break
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            best = n if best is None else max(best, n)
        return best
    if dialect == "postgres":
        best = None
        for row in rows:
            text = " ".join(str(v) for v in row.values()) if isinstance(row, dict) else str(row)
            for match in re.finditer(r"rows=(\d+)", text):
                n = int(match.group(1))
                best = n if best is None else max(best, n)
        return best
    return None


def quote_identifier(name: str, dialect: str = "generic") -> str:
    # MariaDB quotes identifiers with backticks like MySQL by default (double quotes
    # are string literals unless ANSI_QUOTES is set). The rest of the dialect handling
    # (_sql_top_level, SQLGuard) already treats mysql/mariadb together — keep this
    # consistent so a "mariadb" dialect doesn't emit string-literal "identifiers".
    if dialect in ("mysql", "mariadb"):
        q = "`"
    else:
        q = '"'
    parts = [part for part in str(name).split(".") if part]
    if len(parts) > 1:
        return ".".join(q + part.replace(q, q + q) + q for part in parts)
    return q + str(name).replace(q, q + q) + q


def dedupe_columns(names: list[str]) -> list[str]:
    """Disambiguate duplicate result-column names so a dict row can hold them all.

    A query like ``SELECT a.id, b.id FROM a JOIN b`` yields two columns named ``id``;
    keyed into a dict the second would silently overwrite the first, losing data.
    Rename repeats to ``id (2)``, ``id (3)``, … (display labels — schema-level names
    used for follow-up SQL are unaffected). Order-preserving."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for raw in names:
        name = str(raw)
        count = seen.get(name, 0) + 1
        seen[name] = count
        out.append(name if count == 1 else f"{name} ({count})")
    return out


def rows_to_result(rows: list[dict[str, Any]], *, sql: str = "", elapsed_ms: float = 0.0,
                   truncated: bool = False, columns: list[str] | None = None) -> QueryResult:
    if columns is None:
        columns = list(rows[0].keys()) if rows else []
    return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated, sql=sql, elapsed_ms=elapsed_ms)


def _sql_top_level(sql: str, *, dialect: str = "generic") -> str:
    """Return the SQL with string/comment literals and any parenthesised regions
    blanked out, so keyword scans only see the *outer* (top-level) statement."""
    out: list[str] = []
    i, n = 0, len(sql)
    quote = ""
    depth = 0
    # Only MySQL/MariaDB treats backslash as a string escape.
    backslash_escapes = dialect in ("mysql", "mariadb")
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if quote:
            if backslash_escapes and ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            i += 1
            continue
        if ch == "-" and nxt == "-":
            j = sql.find("\n", i + 2)
            i = n if j < 0 else j
            continue
        if ch == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch == "(":
            depth += 1
            out.append(" ")
        elif ch == ")":
            depth = max(0, depth - 1)
            out.append(" ")
        else:
            out.append(" " if depth > 0 else ch)
        i += 1
    return "".join(out)


def outer_limit_value(sql: str, *, dialect: str = "generic") -> int | None:
    """The effective row-count cap of a top-level row limiter, or None if the
    outer query has none. Handles ``LIMIT n``, ``LIMIT offset, count`` (count is
    the cap), ``LIMIT n OFFSET m`` and the SQL-standard ``FETCH FIRST/NEXT n
    ROWS ONLY`` (Postgres/DB2/Oracle). Limiters inside subqueries/CTEs/strings
    are ignored."""
    top = _sql_top_level(sql, dialect=dialect)
    match = re.search(r"\blimit\s+(\d+)(?:\s*,\s*(\d+))?", top, re.I)
    if match:
        # "LIMIT a, b" → b is the row count; "LIMIT n" → n.
        return int(match.group(2) if match.group(2) is not None else match.group(1))
    # SQL-standard: FETCH FIRST|NEXT n ROWS|ROW ONLY (n optional, defaults to 1).
    fetch = re.search(r"\bfetch\s+(?:first|next)\s+(\d+)?\s*rows?\s+only", top, re.I)
    if fetch:
        return int(fetch.group(1)) if fetch.group(1) else 1
    return None


def has_outer_row_limiter(sql: str, *, dialect: str = "generic") -> bool:
    """True if the top-level query already has any row-limiting clause.

    Unlike :func:`outer_limit_value` (which returns a numeric count, or None when
    no *numeric* limit is found), this also recognises the non-numeric limiters
    ``LIMIT ALL`` and ``LIMIT NULL`` (Postgres no-ops) and the SQL-standard
    ``FETCH FIRST/NEXT … ROWS ONLY``. Used to decide whether appending a LIMIT is
    safe: appending after an existing ``LIMIT ALL`` would yield invalid
    double-LIMIT SQL.
    """
    top = _sql_top_level(sql, dialect=dialect)
    if re.search(r"\blimit\b", top, re.I):
        return True
    if re.search(r"\bfetch\s+(?:first|next)\b.*?\brows?\s+only\b", top, re.I | re.S):
        return True
    return False


def append_limit(sql: str, limit: int | None, *, dialect: str = "generic") -> str:
    if limit is None:
        return sql
    stripped = sql.strip().rstrip(";")
    if has_outer_row_limiter(stripped, dialect=dialect):
        return stripped
    # Append on a NEW LINE, not after a space: if the SQL ends with a trailing line
    # comment (``SELECT * FROM t -- note``) a same-line ``LIMIT`` would be swallowed
    # by the comment and the query would run unbounded. A newline puts LIMIT past the
    # comment so the row cap stays effective.
    return f"{stripped}\nLIMIT {int(limit)}"
