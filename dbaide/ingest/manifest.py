"""Provenance manifest for an imported CSV/Excel collection.

One manifest lives next to the generated SQLite file (``imports/{conn}/manifest.json``).
It records which workbook/sheet each table came from and the inferred column types, so
the data stays traceable and a later re-import can target a single workbook. The query
path itself is a plain read-only SQLite connection — the manifest is pure metadata.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1


@dataclass
class ColumnInfo:
    name: str            # sanitized SQLite column identifier
    original: str        # original header text as written in the file
    type: str            # SQLite affinity: "INTEGER" | "REAL" | "TEXT"
    note: str = ""       # optional hint, e.g. "datetime" (reserved for later phases)


@dataclass
class SheetInfo:
    sheet_name: str      # original sheet title (or the CSV file stem)
    table: str           # physical SQLite table name
    display_name: str    # human label shown in the UI
    header_row: int      # 0-based index of the row used as the header
    row_count: int
    columns: list[ColumnInfo] = field(default_factory=list)
    data_bbox: list[int] = field(default_factory=list)        # [r0,c0,r1,c1] of the located table
    filled_columns: list[int] = field(default_factory=list)   # cols filled from vertical merges


@dataclass
class WorkbookInfo:
    id: str              # stable id within the collection (for add/remove/rename targeting)
    name: str            # logical name (drives table names); editable, defaults to file stem
    source_filename: str
    file_hash: str       # sha256 of the source file (change detection / re-import match)
    imported_at: str     # ISO-8601 UTC
    sheets: list[SheetInfo] = field(default_factory=list)
    source_path: str = ""    # original file path, for re-importing if it still exists


@dataclass
class ImportManifest:
    version: int = MANIFEST_VERSION
    workbooks: list[WorkbookInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ImportManifest":
        workbooks = []
        for wb in (data.get("workbooks") or []):
            sheets = [
                SheetInfo(
                    sheet_name=str(s.get("sheet_name") or ""),
                    table=str(s.get("table") or ""),
                    display_name=str(s.get("display_name") or s.get("sheet_name") or ""),
                    header_row=int(s.get("header_row") or 0),
                    row_count=int(s.get("row_count") or 0),
                    data_bbox=[int(x) for x in (s.get("data_bbox") or [])],
                    filled_columns=[int(x) for x in (s.get("filled_columns") or [])],
                    columns=[
                        ColumnInfo(
                            name=str(c.get("name") or ""),
                            original=str(c.get("original") or ""),
                            type=str(c.get("type") or "TEXT"),
                            note=str(c.get("note") or ""),
                        )
                        for c in (s.get("columns") or [])
                    ],
                )
                for s in (wb.get("sheets") or [])
            ]
            source_filename = str(wb.get("source_filename") or "")
            workbooks.append(WorkbookInfo(
                id=str(wb.get("id") or ""),
                name=str(wb.get("name") or "") or Path(source_filename).stem,
                source_filename=source_filename,
                file_hash=str(wb.get("file_hash") or ""),
                imported_at=str(wb.get("imported_at") or ""),
                sheets=sheets,
                source_path=str(wb.get("source_path") or ""),
            ))
        return cls(version=int(data.get("version") or MANIFEST_VERSION), workbooks=workbooks)

    def save(self, path: Path) -> None:
        # Atomic write (tempfile + os.replace), matching the config/board/annotation
        # stores: a crash mid-write must not leave a truncated manifest.json that fails
        # to parse and orphans the imported tables.
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
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

    @classmethod
    def load(cls, path: Path) -> "ImportManifest":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
