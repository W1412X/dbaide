from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from dbaide.models import ColumnInfo, ColumnProfile, TableInfo


class DisclosureLevel(IntEnum):
    NONE = 0
    DATABASES = 1
    TABLES = 2
    COLUMNS = 3
    PROFILE = 4
    EXECUTION = 5


@dataclass(slots=True)
class TableDisclosure:
    table: TableInfo
    instance: str = ""
    database: str = ""
    columns: list[ColumnInfo] = field(default_factory=list)
    profiles: dict[str, ColumnProfile] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class DisclosureContext:
    instance: str = ""
    databases: list[str] = field(default_factory=list)
    tables: dict[str, TableDisclosure] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)

    def set_instance(self, instance: str) -> None:
        self.instance = instance
        self.events.append(f"L0 instance disclosed: {instance or '(none)'}")

    def record_databases(self, databases: list[str]) -> None:
        self.databases = list(dict.fromkeys(databases))
        self.events.append(f"L1 databases disclosed: {', '.join(self.databases) or '(none)'}")

    def record_tables(self, tables: list[TableInfo], *, database: str = "") -> None:
        for table in tables:
            db = database or table.schema or ""
            ref = self.table_ref(db, table.name)
            self.tables.setdefault(ref, TableDisclosure(table=table, instance=self.instance, database=db))
        prefix = self.path(database)
        self.events.append(f"L2 tables disclosed: {prefix} ({len(tables)} table(s))")

    def record_columns(self, table: str, columns: list[ColumnInfo], *, database: str = "") -> None:
        entry = self._entry_for(table, database=database)
        if entry is None:
            ref = self.table_ref(database, table)
            entry = TableDisclosure(table=TableInfo(name=table, schema=database), instance=self.instance, database=database)
            self.tables[ref] = entry
        entry.columns = list(columns)
        self.events.append(f"L3 columns disclosed: {self.path(database, table)} ({len(columns)} column(s))")

    def record_profile(self, table: str, profile: ColumnProfile, *, database: str = "") -> None:
        entry = self._entry_for(table, database=database)
        if entry:
            entry.profiles[profile.column] = profile
        self.events.append(f"L4 profile disclosed: {self.path(database, table, profile.column)}")

    def record_samples(self, table: str, rows: list[dict[str, Any]], *, database: str = "") -> None:
        entry = self._entry_for(table, database=database)
        if entry:
            entry.samples = list(rows)
        self.events.append(f"L4 samples disclosed: {self.path(database, table)} ({len(rows)} row(s))")

    def record_execution(self, sql: str, *, database: str = "") -> None:
        preview = " ".join(sql.split())[:160]
        prefix = self.path(database)
        self.events.append(f"L5 execution evidence disclosed: {prefix}: {preview}")

    def known_columns(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for ref, entry in self.tables.items():
            if entry.columns:
                col_names = {c.name for c in entry.columns}
                out[ref] = col_names
                bare = entry.table.name
                if bare in out:
                    out[bare] = out[bare] | col_names
                else:
                    out[bare] = col_names
        return out

    def table_names(self) -> list[str]:
        return [entry.table.name for entry in self.tables.values()]

    def summary(self) -> dict[str, Any]:
        return {
            "instances": [self.instance] if self.instance else [],
            "databases": self.databases,
            "tables": [
                {
                    "instance": entry.instance,
                    "database": entry.database,
                    "path": self.table_ref(entry.database, entry.table.name),
                    "name": entry.table.name,
                    "schema": entry.table.schema,
                    "comment": entry.table.comment,
                    "estimated_rows": entry.table.estimated_rows,
                    "columns": [
                        {
                            "name": col.name,
                            "type": col.data_type,
                            "comment": col.comment,
                            "pk": col.primary_key,
                            "indexed": col.indexed,
                        }
                        for col in entry.columns
                    ],
                    "profiles": {
                        name: {
                            "null_count": prof.null_count,
                            "distinct_count": prof.distinct_count,
                            "min": prof.min_value,
                            "max": prof.max_value,
                            "top_values": prof.top_values[:5],
                        }
                        for name, prof in entry.profiles.items()
                    },
                }
                for entry in self.tables.values()
            ],
        }

    def _entry_for(self, table: str, *, database: str = "") -> TableDisclosure | None:
        ref = self.table_ref(database, table)
        if ref in self.tables:
            return self.tables[ref]
        if table in self.tables:
            return self.tables[table]
        for entry in self.tables.values():
            database_ok = not database or entry.database == database or entry.table.schema == database
            if database_ok and (entry.table.name == table or entry.table.ref == table):
                return entry
        return None

    def table_ref(self, database: str, table: str) -> str:
        parts = [p for p in [database, table] if p]
        return ".".join(parts)

    def path(self, *parts: str) -> str:
        clean = [str(p) for p in parts if p]
        return ".".join(clean) if clean else "(current)"
