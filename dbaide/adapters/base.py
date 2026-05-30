from __future__ import annotations

import re
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Iterator

from dbaide.models import ColumnInfo, ColumnProfile, ConnectionConfig, ForeignKeyInfo, QueryResult, TableInfo


class DatabaseAdapter(ABC):
    dialect = "generic"

    def __init__(self, config: ConnectionConfig) -> None:
        self.config = config

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

    @abstractmethod
    def execute_readonly(self, sql: str, *, database: str = "", limit: int | None = None, timeout_seconds: int = 10) -> QueryResult:
        raise NotImplementedError

    @abstractmethod
    def explain(self, sql: str, *, database: str = "", timeout_seconds: int = 10) -> QueryResult:
        raise NotImplementedError

    @abstractmethod
    def sample_rows(self, table: str, *, database: str = "", limit: int = 20) -> QueryResult:
        raise NotImplementedError

    @abstractmethod
    def profile_column(self, table: str, column: str, *, database: str = "", top_k: int = 10,
                       timeout_seconds: int = 30) -> ColumnProfile:
        raise NotImplementedError

    @contextmanager
    def lifecycle(self) -> Iterator["DatabaseAdapter"]:
        yield self


def quote_identifier(name: str, dialect: str = "generic") -> str:
    if dialect == "mysql":
        q = "`"
    else:
        q = '"'
    parts = [part for part in str(name).split(".") if part]
    if len(parts) > 1:
        return ".".join(q + part.replace(q, q + q) + q for part in parts)
    return q + str(name).replace(q, q + q) + q


def rows_to_result(rows: list[dict[str, Any]], *, sql: str = "", elapsed_ms: float = 0.0, truncated: bool = False) -> QueryResult:
    columns: list[str] = []
    if rows:
        columns = list(rows[0].keys())
    return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated, sql=sql, elapsed_ms=elapsed_ms)


def append_limit(sql: str, limit: int | None) -> str:
    if limit is None:
        return sql
    stripped = sql.strip().rstrip(";")
    if re.search(r"\blimit\s+\d+\b", stripped, re.I):
        return stripped
    return f"{stripped} LIMIT {int(limit)}"
