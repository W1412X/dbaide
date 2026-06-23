"""Import CSV/Excel workbooks into a local SQLite database.

Phase 1: one clean rectangular table per sheet (header on the first non-empty row),
written to a single ``data.db`` so cross-sheet / cross-workbook joins are free. A
``manifest.json`` records provenance and inferred column types. The result is a normal
read-only SQLite database — register it as a ``sqlite`` connection and everything else
(agent, charts, safety) works unchanged.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dbaide.ingest.manifest import ColumnInfo, ImportManifest, SheetInfo, WorkbookInfo
from dbaide.ingest.readers import RawSheet, read_workbook


@dataclass
class ImportResult:
    db_path: Path
    manifest: ImportManifest
    warnings: list[str] = field(default_factory=list)

    @property
    def table_count(self) -> int:
        return sum(len(w.sheets) for w in self.manifest.workbooks)

    @property
    def total_rows(self) -> int:
        return sum(s.row_count for w in self.manifest.workbooks for s in w.sheets)


def import_workbooks(
    paths: list[Path | str],
    *,
    dest_dir: Path | str,
    append: bool = False,
    now: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> ImportResult:
    """Import one or more files into ``dest_dir/data.db`` and write the manifest.

    ``append=False`` (default) rebuilds the collection from scratch. ``append=True`` adds
    the files to an existing collection, keeping table names unique against what's already
    there. The new tables are written in a single transaction; on failure the existing
    collection is left untouched (no partial tables, manifest unchanged).
    """
    files = [Path(p) for p in paths]
    if not files:
        raise ValueError("no files to import")
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    db_path = dest / "data.db"
    manifest_path = dest / "manifest.json"

    if append and manifest_path.exists():
        manifest = ImportManifest.load(manifest_path)
    else:
        manifest = ImportManifest()
        db_path.unlink(missing_ok=True)
    used_tables = {s.table for w in manifest.workbooks for s in w.sheets}
    stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")

    new_workbooks: list[WorkbookInfo] = []
    conn = sqlite3.connect(db_path)
    try:
        for index, path in enumerate(files):
            workbook = read_workbook(path)
            file_slug = _slug(path.stem, fallback="data")
            file_hash = _file_hash(path)
            wb_info = WorkbookInfo(
                id=_workbook_id(file_hash, stamp, index),
                source_filename=workbook.filename,
                file_hash=file_hash,
                imported_at=stamp,
            )
            single = len(workbook.sheets) == 1
            for sheet in workbook.sheets:
                table = _unique(_table_name(file_slug, sheet.name), used_tables)
                columns = _plan_columns(sheet)
                _write_table(conn, table, columns, sheet.rows)
                display = (
                    path.stem if single and _slug(sheet.name, fallback="") == file_slug
                    else f"{path.stem} · {sheet.name}"
                )
                wb_info.sheets.append(SheetInfo(
                    sheet_name=sheet.name,
                    table=table,
                    display_name=display,
                    header_row=sheet.header_row,
                    row_count=len(sheet.rows),
                    columns=columns,
                ))
                if on_progress:
                    on_progress(f"{path.name} · {sheet.name} → {table} ({len(sheet.rows)} rows)")
            new_workbooks.append(wb_info)
        conn.commit()
    except BaseException:
        conn.close()
        if not append:
            db_path.unlink(missing_ok=True)   # fresh import failed → no half-written db
        raise
    conn.close()

    manifest.workbooks.extend(new_workbooks)
    manifest.save(manifest_path)
    return ImportResult(db_path=db_path, manifest=manifest)


def remove_workbook(dest_dir: Path | str, workbook_id: str) -> ImportManifest:
    """Drop a workbook's tables and remove it from the manifest. Returns the new manifest.
    Raises KeyError if the id isn't present."""
    dest = Path(dest_dir)
    manifest = ImportManifest.load(dest / "manifest.json")
    target = next((w for w in manifest.workbooks if w.id == workbook_id), None)
    if target is None:
        raise KeyError(workbook_id)
    conn = sqlite3.connect(dest / "data.db")
    try:
        for sheet in target.sheets:
            conn.execute(f"DROP TABLE IF EXISTS {_q(sheet.table)}")
        conn.commit()
    finally:
        conn.close()
    manifest.workbooks = [w for w in manifest.workbooks if w.id != workbook_id]
    manifest.save(dest / "manifest.json")
    return manifest


# ── column planning / type inference ─────────────────────────────────────────

def _plan_columns(sheet: RawSheet) -> list[ColumnInfo]:
    names = _column_names(sheet.header)
    columns: list[ColumnInfo] = []
    for i, name in enumerate(names):
        values = [row[i] for row in sheet.rows]
        original = sheet.header[i] if (i < len(sheet.header) and sheet.header[i]) else f"col_{i + 1}"
        columns.append(ColumnInfo(name=name, original=str(original), type=_infer_affinity(values)))
    return columns


def _infer_affinity(values: list[Any]) -> str:
    seen = [v for v in values if not _is_blank(v)]
    if not seen:
        return "TEXT"
    if any(_looks_like_code(v) for v in seen):   # preserve leading-zero ids/zips/phones
        return "TEXT"
    if all(_try_int(v) is not None for v in seen):
        return "INTEGER"
    if all(_try_float(v) is not None for v in seen):
        return "REAL"
    return "TEXT"


_INT_RE = re.compile(r"[+-]?\d+$")
_FLOAT_RE = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _looks_like_code(v: Any) -> bool:
    return isinstance(v, str) and len(v) > 1 and v[0] == "0" and v.isdigit()


def _try_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if float(v).is_integer() else None
    if isinstance(v, str) and _INT_RE.match(v.strip()):
        return int(v.strip())
    return None


def _try_float(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s and _FLOAT_RE.match(s):
            return float(s)
    return None


def _coerce(value: Any, affinity: str) -> Any:
    if _is_blank(value):
        return None
    if affinity == "INTEGER":
        iv = _try_int(value)
        return iv if iv is not None else _as_text(value)
    if affinity == "REAL":
        fv = _try_float(value)
        return fv if fv is not None else _as_text(value)
    return _as_text(value)


def _as_text(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value.strip() if isinstance(value, str) else str(value)


# ── identifiers / writing ────────────────────────────────────────────────────

def _slug(text: Any, *, fallback: str) -> str:
    s = re.sub(r"\s+", "_", str(text or "").strip())
    s = re.sub(r"[^\w]", "_", s)        # \w keeps CJK/letters/digits/_, drops punctuation
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return fallback
    return f"{fallback}_{s}" if s[0].isdigit() else s


def _column_names(header: list[str]) -> list[str]:
    out: list[str] = []
    used: set[str] = set()
    for i, h in enumerate(header):
        out.append(_unique(_slug(h, fallback=f"col_{i + 1}"), used))
    return out


def _table_name(file_slug: str, sheet_name: str) -> str:
    # Namespace a sheet by its workbook, but collapse the redundant CSV case where the
    # only sheet is named after the file (sales.csv → "sales", not "sales__sales").
    sheet_slug = _slug(sheet_name, fallback="sheet")
    return sheet_slug if sheet_slug == file_slug else f"{file_slug}__{sheet_slug}"


def _workbook_id(file_hash: str, stamp: str, index: int) -> str:
    return "wb_" + hashlib.sha256(f"{file_hash}|{stamp}|{index}".encode()).hexdigest()[:8]


def _unique(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    k = 2
    while f"{name}_{k}" in used:
        k += 1
    final = f"{name}_{k}"
    used.add(final)
    return final


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _write_table(conn: sqlite3.Connection, table: str, columns: list[ColumnInfo], rows: list[list[Any]]) -> None:
    coldefs = ", ".join(f"{_q(c.name)} {c.type}" for c in columns)
    conn.execute(f"DROP TABLE IF EXISTS {_q(table)}")
    conn.execute(f"CREATE TABLE {_q(table)} ({coldefs})")
    placeholders = ", ".join("?" for _ in columns)
    payload = [[_coerce(row[i], columns[i].type) for i in range(len(columns))] for row in rows]
    conn.executemany(f"INSERT INTO {_q(table)} VALUES ({placeholders})", payload)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
