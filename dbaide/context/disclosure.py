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
    instances: list[str] = field(default_factory=list)
    databases: dict[str, list[str]] = field(default_factory=dict)
    tables: dict[str, TableDisclosure] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)

    def record_instances(self, instances: list[str]) -> None:
        self.instances = list(dict.fromkeys(instances))
        self.events.append(f"L0 instances disclosed: {', '.join(self.instances) or '(none)'}")

    def record_databases(self, instance: str, databases: list[str]) -> None:
        self._ensure_instance(instance)
        self.databases[instance] = list(dict.fromkeys(databases))
        self.events.append(f"L1 databases disclosed: {instance} -> {', '.join(databases) or '(none)'}")

    def record_tables(self, tables: list[TableInfo], *, instance: str = "", database: str = "") -> None:
        self._ensure_instance(instance)
        for table in tables:
            db = database or table.schema or ""
            ref = self.table_ref(instance, db, table.name)
            self.tables.setdefault(ref, TableDisclosure(table=table, instance=instance, database=db))
        prefix = self.path(instance, database)
        self.events.append(f"L2 tables disclosed: {prefix} ({len(tables)} table(s))")

    def record_columns(self, table: str, columns: list[ColumnInfo], *, instance: str = "", database: str = "") -> None:
        entry = self._entry_for(table, instance=instance, database=database)
        if entry is None:
            # describe_table may be reached without a prior list_tables/discover
            # (e.g. a direct table question, or a resumed loop). Register the table
            # here so SchemaGuard stays fail-closed instead of silently dropping it.
            ref = self.table_ref(instance, database, table)
            entry = TableDisclosure(table=TableInfo(name=table, schema=database), instance=instance, database=database)
            self.tables[ref] = entry
        entry.columns = list(columns)
        self.events.append(f"L3 columns disclosed: {self.path(instance, database, table)} ({len(columns)} column(s))")

    def record_profile(self, table: str, profile: ColumnProfile, *, instance: str = "", database: str = "") -> None:
        entry = self._entry_for(table, instance=instance, database=database)
        if entry:
            entry.profiles[profile.column] = profile
        self.events.append(f"L4 profile disclosed: {self.path(instance, database, table, profile.column)}")

    def record_samples(self, table: str, rows: list[dict[str, Any]], *, instance: str = "", database: str = "") -> None:
        entry = self._entry_for(table, instance=instance, database=database)
        if entry:
            entry.samples = list(rows)
        self.events.append(f"L4 samples disclosed: {self.path(instance, database, table)} ({len(rows)} row(s))")

    def record_execution(self, sql: str, *, instance: str = "", database: str = "") -> None:
        preview = " ".join(sql.split())[:160]
        prefix = self.path(instance, database)
        self.events.append(f"L5 execution evidence disclosed: {prefix}: {preview}")

    def known_columns(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for ref, entry in self.tables.items():
            if entry.columns:
                out[ref] = {c.name for c in entry.columns}
                out[entry.table.name] = {c.name for c in entry.columns}
        return out

    def table_names(self) -> list[str]:
        return [entry.table.name for entry in self.tables.values()]

    def summary(self) -> dict[str, Any]:
        return {
            "instances": self.instances,
            "databases": self.databases,
            "tables": [
                {
                    "instance": entry.instance,
                    "database": entry.database,
                    "path": self.table_ref(entry.instance, entry.database, entry.table.name),
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

    def _entry_for(self, table: str, *, instance: str = "", database: str = "") -> TableDisclosure | None:
        ref = self.table_ref(instance, database, table)
        if ref in self.tables:
            return self.tables[ref]
        if table in self.tables:
            return self.tables[table]
        for entry in self.tables.values():
            instance_ok = not instance or entry.instance == instance
            database_ok = not database or entry.database == database or entry.table.schema == database
            if instance_ok and database_ok and (entry.table.name == table or entry.table.ref == table):
                return entry
        return None

    def _ensure_instance(self, instance: str) -> None:
        if instance and instance not in self.instances:
            self.instances.append(instance)

    def table_ref(self, instance: str, database: str, table: str) -> str:
        parts = [p for p in [instance, database, table] if p]
        return ".".join(parts)

    def path(self, *parts: str) -> str:
        clean = [str(p) for p in parts if p]
        return ".".join(clean) if clean else "(current)"
