from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from dbaide.models import ColumnInfo, TableInfo

logger = logging.getLogger("dbaide.assets")

DEFAULT_ASSET_DIR = Path.home() / ".dbaide" / "assets"


class AssetStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = Path(os.environ.get("DBAIDE_ASSETS", base_dir or DEFAULT_ASSET_DIR)).expanduser()

    def instance_dir(self, instance: str) -> Path:
        return self.base_dir / "instances" / safe_name(instance)

    def purge_instance(self, instance: str) -> bool:
        """Delete all offline assets for a connection (used when it is removed)."""
        path = self.instance_dir(instance)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            return True
        return False

    def delete_table(self, instance: str, database: str, table: str) -> bool:
        """Delete one table's doc dir (used when a refresh finds it dropped)."""
        path = self.table_dir(instance, database, table)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            return True
        return False

    def delete_database(self, instance: str, database: str) -> bool:
        """Delete one database's doc dir (used when a refresh finds it dropped)."""
        path = self.database_dir(instance, database)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            return True
        return False

    def database_dir(self, instance: str, database: str) -> Path:
        return self.instance_dir(instance) / "databases" / safe_name(database)

    def table_dir(self, instance: str, database: str, table: str) -> Path:
        return self.database_dir(instance, database) / "tables" / safe_name(table)

    def column_dir(self, instance: str, database: str, table: str) -> Path:
        return self.table_dir(instance, database, table) / "columns"

    def column_path(self, instance: str, database: str, table: str, column: str) -> Path:
        """Per-column doc file. The column name is sanitized like every other path
        component — a quoted identifier could legitimately contain '/' or '..', which
        must not escape the table's columns dir."""
        return self.column_dir(instance, database, table) / f"{safe_name(column)}.json"

    def write_json(self, path: Path, data: dict[str, Any] | list[Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def has_instance(self, instance: str) -> bool:
        return (self.instance_dir(instance) / "instance.json").exists()

    def instance_doc(self, instance: str) -> dict[str, Any] | None:
        return self._read_optional(self.instance_dir(instance) / "instance.json")

    def database_docs(self, instance: str) -> list[dict[str, Any]]:
        path = self.instance_dir(instance) / "databases.json"
        data = self._read_optional(path)
        if isinstance(data, dict):
            return list(data.get("databases") or [])
        return []

    def table_docs(self, instance: str, database: str) -> list[dict[str, Any]]:
        path = self.database_dir(instance, database) / "tables.json"
        data = self._read_optional(path)
        if isinstance(data, dict):
            return list(data.get("tables") or [])
        return []

    def column_docs(self, instance: str, database: str, table: str) -> list[dict[str, Any]]:
        """Derive per-column views from the table doc (the disclosure leaf). There
        are no per-column files anymore; this keeps a stable shape for callers that
        iterate columns (schema tree, describe_table, search, dev tools)."""
        tdoc = self.table_doc(instance, database, table)
        if not tdoc:
            return []
        indexed: set[str] = set()
        index_map: dict[str, list[dict[str, Any]]] = {}
        for ix in tdoc.get("indexes") or []:
            for cn in ix.get("columns") or []:
                indexed.add(cn)
                index_map.setdefault(cn, []).append(ix)
        out: list[dict[str, Any]] = []
        for col in tdoc.get("columns") or []:
            name = col.get("name")
            out.append({
                "kind": "column",
                "instance": instance, "database": database, "table": table,
                "column": name, "name": name,
                "data_type": col.get("data_type"),
                "nullable": col.get("nullable"),
                "default": col.get("default"),
                "primary_key": col.get("primary_key"),
                "source_comment": col.get("comment"),
                "indexed": name in indexed,
                "indexes": index_map.get(name, []),
            })
        return out

    def table_doc(self, instance: str, database: str, table: str) -> dict[str, Any] | None:
        return self._read_optional(self.table_dir(instance, database, table) / "table.json")

    def to_table_info(self, doc: dict[str, Any]) -> TableInfo:
        return TableInfo(
            name=str(doc.get("name") or doc.get("table") or ""),
            schema=str(doc.get("database") or doc.get("schema") or ""),
            comment=str(doc.get("description") or doc.get("comment") or ""),
            estimated_rows=doc.get("row_count") if doc.get("row_count") is not None else doc.get("estimated_rows"),
            table_type=str(doc.get("table_type") or "table"),
        )

    def to_column_info(self, doc: dict[str, Any]) -> ColumnInfo:
        return ColumnInfo(
            name=str(doc.get("name") or doc.get("column") or ""),
            data_type=str(doc.get("data_type") or doc.get("type") or ""),
            nullable=doc.get("nullable"),
            default=doc.get("default"),
            comment=str(doc.get("source_comment") or doc.get("comment") or ""),
            primary_key=bool(doc.get("primary_key")),
            indexed=bool(doc.get("indexed")),
        )

    def _read_optional(self, path: Path) -> Any:
        if not path.exists():
            return None
        try:
            return self.read_json(path)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None


def safe_name(value: str) -> str:
    text = str(value or "default").strip()
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text)
    text = text.strip("_.-")
    if not text or text == "." or text == "..":
        return "default"
    return text

