"""Backup format writers — each writes rows to a local file in a specific format."""
from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path
from typing import Any

from dbaide.adapters.base import quote_identifier
from dbaide.models import ColumnInfo


class CsvWriter:
    suffix = ".csv"

    def __init__(self, path: Path, columns: list[str], column_infos: list[ColumnInfo] | None = None,
                 *, dialect: str = "generic") -> None:
        self._path = path
        self._columns = columns
        self._fh = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(columns)
        self._rows_written = 0

    def write_rows(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self._writer.writerow([row.get(c) for c in self._columns])
        self._rows_written += len(rows)

    def close(self) -> int:
        self._fh.close()
        return self._rows_written

    @property
    def file_size(self) -> int:
        return os.path.getsize(self._path) if self._path.exists() else 0


class SqlWriter:
    suffix = ".sql"

    def __init__(self, path: Path, columns: list[str], column_infos: list[ColumnInfo] | None = None,
                 *, dialect: str = "generic", table_name: str = "", ddl: str = "") -> None:
        self._path = path
        self._columns = columns
        self._dialect = dialect
        self._table = table_name
        self._fh = open(path, "w", encoding="utf-8")
        if ddl:
            self._fh.write(ddl.rstrip().rstrip(";") + ";\n\n")
        elif column_infos:
            self._fh.write(self._generate_ddl(column_infos) + "\n\n")
        self._rows_written = 0

    def _generate_ddl(self, infos: list[ColumnInfo]) -> str:
        lines = []
        for col in infos:
            line = f"  {quote_identifier(col.name, self._dialect)} {col.data_type or 'TEXT'}"
            if col.primary_key:
                line += " PRIMARY KEY"
            if col.nullable is False:
                line += " NOT NULL"
            if col.default is not None:
                line += f" DEFAULT {col.default}"
            lines.append(line)
        tname = quote_identifier(self._table, self._dialect) if self._table else '"table"'
        return f"CREATE TABLE {tname} (\n" + ",\n".join(lines) + "\n);"

    def write_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        tname = quote_identifier(self._table, self._dialect) if self._table else '"table"'
        col_list = ", ".join(quote_identifier(c, self._dialect) for c in self._columns)
        for row in rows:
            vals = ", ".join(self._sql_literal(row.get(c)) for c in self._columns)
            self._fh.write(f"INSERT INTO {tname} ({col_list}) VALUES ({vals});\n")
        self._rows_written += len(rows)

    @staticmethod
    def _sql_literal(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        s = str(value).replace("'", "''")
        return f"'{s}'"

    def close(self) -> int:
        self._fh.close()
        return self._rows_written

    @property
    def file_size(self) -> int:
        return os.path.getsize(self._path) if self._path.exists() else 0


class SqliteWriter:
    suffix = ".sqlite"

    def __init__(self, path: Path, columns: list[str], column_infos: list[ColumnInfo] | None = None,
                 *, dialect: str = "generic", table_name: str = "") -> None:
        self._path = path
        self._columns = columns
        self._table = table_name or "data"
        self._conn = sqlite3.connect(str(path))
        col_defs = []
        type_map = {ci.name: ci.data_type for ci in (column_infos or [])}
        for c in columns:
            dtype = self._map_type(type_map.get(c, "TEXT"))
            col_defs.append(f'"{c}" {dtype}')
        create = f'CREATE TABLE IF NOT EXISTS "{self._table}" ({", ".join(col_defs)});'
        self._conn.execute(create)
        self._conn.commit()
        placeholders = ", ".join("?" for _ in columns)
        self._insert_sql = f'INSERT INTO "{self._table}" ({", ".join(f"{chr(34)}{c}{chr(34)}" for c in columns)}) VALUES ({placeholders});'
        self._rows_written = 0

    @staticmethod
    def _map_type(dtype: str) -> str:
        if not dtype:
            return "TEXT"
        upper = dtype.upper().split("(")[0].strip()
        if upper in {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT", "SERIAL", "BIGSERIAL"}:
            return "INTEGER"
        if upper in {"REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "DOUBLE PRECISION"}:
            return "REAL"
        if upper in {"BLOB", "BYTEA", "BINARY", "VARBINARY", "LONGBLOB", "MEDIUMBLOB", "TINYBLOB"}:
            return "BLOB"
        return "TEXT"

    def write_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        data = [tuple(row.get(c) for c in self._columns) for row in rows]
        self._conn.executemany(self._insert_sql, data)
        self._conn.commit()
        self._rows_written += len(rows)

    def close(self) -> int:
        self._conn.close()
        return self._rows_written

    @property
    def file_size(self) -> int:
        return os.path.getsize(self._path) if self._path.exists() else 0


FORMATS: dict[str, type[CsvWriter | SqlWriter | SqliteWriter]] = {
    "csv": CsvWriter,
    "sql": SqlWriter,
    "sqlite": SqliteWriter,
}
