"""Import CSV/Excel workbooks into a local SQLite database.

Phase 1: one clean rectangular table per sheet (header on the first non-empty row),
written to a single ``data.db`` so cross-sheet / cross-workbook joins are free. A
``manifest.json`` records provenance and inferred column types. The result is a normal
read-only SQLite database — register it as a ``sqlite`` connection and everything else
(agent, charts, safety) works unchanged.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dbaide.ingest.manifest import ColumnInfo, ImportManifest, SheetInfo, WorkbookInfo
from dbaide.ingest.readers import RawSheet, read_workbook


@dataclass
class ImportSpec:
    """A file to import, with an optional logical name. The logical name (defaulting to the
    file stem) is the editable identity of the workbook and drives its table name(s)."""
    path: Path
    name: str = ""
    # sheet name → user-chosen (header_row, start_col) anchor, 0-based
    header_anchors: dict[str, tuple[int, int]] | None = None
    sheets: list[str] | None = None             # restrict import to these sheets (None = all)

    @property
    def logical_name(self) -> str:
        return (self.name or "").strip() or Path(self.path).stem


def _as_spec(item: "Path | str | ImportSpec") -> ImportSpec:
    return item if isinstance(item, ImportSpec) else ImportSpec(Path(item))


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


@contextlib.contextmanager
def _writer(db_path: Path) -> Iterator[sqlite3.Connection]:
    """One explicit transaction spanning DDL + DML, so a failed batch leaves the database
    untouched. The sqlite3 module's legacy mode auto-commits before DDL (CREATE/DROP/ALTER),
    which would otherwise leave partial schema changes committed on error; we manage the
    transaction ourselves (isolation_level=None + explicit BEGIN). SQLite DDL is transactional,
    so the ROLLBACK undoes table creates/drops/renames too."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def import_workbooks(
    items: "list[Path | str | ImportSpec]",
    *,
    dest_dir: Path | str,
    append: bool = False,
    overwrite: bool = False,
    now: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> ImportResult:
    """Import one or more files into ``dest_dir/data.db`` and write the manifest.

    Each item is a path or an :class:`ImportSpec` carrying a logical name. ``append=False``
    (default) rebuilds the collection from scratch; ``append=True`` adds to an existing one.
    With ``overwrite=True`` an incoming workbook whose logical name matches an existing one
    replaces it (a quick delete-then-add); otherwise table names are de-duplicated. The new
    tables are written in a single transaction; on failure the collection is left untouched.
    """
    specs = [_as_spec(it) for it in items]
    if not specs:
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

    stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_workbooks: list[WorkbookInfo] = []
    warnings: list[str] = []
    try:
        with _writer(db_path) as conn:
            if overwrite:
                incoming = {s.logical_name for s in specs}
                for wb in [w for w in manifest.workbooks if w.name in incoming]:
                    for sheet in wb.sheets:
                        conn.execute(f"DROP TABLE IF EXISTS {_q(sheet.table)}")
                    manifest.workbooks.remove(wb)
            used_tables = {s.table for w in manifest.workbooks for s in w.sheets}
            for index, spec in enumerate(specs):
                path = Path(spec.path)
                workbook = read_workbook(path, header_overrides=spec.header_anchors, sheets=spec.sheets)
                warnings.extend(f"{path.name} · {w}" for w in workbook.warnings)
                logical = spec.logical_name
                file_hash = _file_hash(path)
                single = len(workbook.sheets) == 1
                wb_info = WorkbookInfo(
                    id=_workbook_id(file_hash, stamp, index, logical),
                    name=logical,
                    source_filename=workbook.filename,
                    file_hash=file_hash,
                    imported_at=stamp,
                    source_path=str(path),
                )
                for sheet in workbook.sheets:
                    table = _unique(_table_name(logical, sheet.name, single), used_tables)
                    columns = _plan_columns(sheet)
                    _write_table(conn, table, columns, sheet.rows)
                    wb_info.sheets.append(SheetInfo(
                        sheet_name=sheet.name,
                        table=table,
                        display_name=_display_name(logical, sheet.name, single),
                        header_row=sheet.header_row,
                        row_count=len(sheet.rows),
                        columns=columns,
                        data_bbox=list(sheet.data_bbox),
                        filled_columns=list(sheet.filled_columns),
                    ))
                    if on_progress:
                        on_progress(f"{path.name} · {sheet.name} → {table} ({len(sheet.rows)} rows)")
                new_workbooks.append(wb_info)
    except BaseException:
        if not append:
            db_path.unlink(missing_ok=True)   # fresh import failed → no half-written db
        raise

    manifest.workbooks.extend(new_workbooks)
    manifest.save(manifest_path)
    return ImportResult(db_path=db_path, manifest=manifest, warnings=warnings)


def rename_workbook(dest_dir: Path | str, workbook_id: str, new_name: str) -> ImportManifest:
    """Rename a workbook's logical name, renaming its SQLite table(s) and updating the
    manifest. Raises KeyError for an unknown id, ValueError for an empty name."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("name must not be empty")
    dest = Path(dest_dir)
    manifest = ImportManifest.load(dest / "manifest.json")
    target = next((w for w in manifest.workbooks if w.id == workbook_id), None)
    if target is None:
        raise KeyError(workbook_id)
    used = {s.table for w in manifest.workbooks if w.id != workbook_id for s in w.sheets}
    single = len(target.sheets) == 1
    with _writer(dest / "data.db") as conn:
        for sheet in target.sheets:
            new_table = _unique(_table_name(new_name, sheet.sheet_name, single), used)
            if new_table != sheet.table:
                conn.execute(f"ALTER TABLE {_q(sheet.table)} RENAME TO {_q(new_table)}")
                sheet.table = new_table
            sheet.display_name = _display_name(new_name, sheet.sheet_name, single)
    target.name = new_name
    manifest.save(dest / "manifest.json")
    return manifest


def remove_workbook(dest_dir: Path | str, workbook_id: str) -> ImportManifest:
    """Drop a workbook's tables and remove it from the manifest. Returns the new manifest.
    Raises KeyError if the id isn't present."""
    dest = Path(dest_dir)
    manifest = ImportManifest.load(dest / "manifest.json")
    target = next((w for w in manifest.workbooks if w.id == workbook_id), None)
    if target is None:
        raise KeyError(workbook_id)
    with _writer(dest / "data.db") as conn:
        for sheet in target.sheets:
            conn.execute(f"DROP TABLE IF EXISTS {_q(sheet.table)}")
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
    if any(_overflows_int(v) for v in seen):     # big numeric ids: keep exact as TEXT, not lossy REAL
        return "TEXT"
    if all(_try_int(v) is not None for v in seen):
        return "INTEGER"
    if all(_try_float(v) is not None for v in seen):
        return "REAL"
    return "TEXT"


_INT_RE = re.compile(r"[+-]?\d+$")
_FLOAT_RE = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _looks_like_code(v: Any) -> bool:
    return isinstance(v, str) and len(v) > 1 and v[0] == "0" and v.isdigit()


def _overflows_int(v: Any) -> bool:
    """True for an integer-shaped value whose magnitude won't fit in SQLite's signed 64-bit
    INTEGER — storing it as INTEGER would raise OverflowError, and as REAL would lose digits,
    so such a column is kept as TEXT."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return not (_INT64_MIN <= v <= _INT64_MAX)
    if isinstance(v, str) and _INT_RE.match(v.strip()):
        return not (_INT64_MIN <= int(v.strip()) <= _INT64_MAX)
    return False


def _try_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        n = v
    elif isinstance(v, float):
        n = int(v) if float(v).is_integer() else None
    elif isinstance(v, str) and _INT_RE.match(v.strip()):
        n = int(v.strip())
    else:
        n = None
    return n if (n is not None and _INT64_MIN <= n <= _INT64_MAX) else None


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


def _table_name(logical_name: str, sheet_name: str, single: bool) -> str:
    # A single-sheet workbook becomes one table named after the workbook; a multi-sheet
    # one namespaces each sheet under the workbook name (name__sheet).
    name_slug = _slug(logical_name, fallback="data")
    if single:
        return name_slug
    return f"{name_slug}__{_slug(sheet_name, fallback='sheet')}"


def _display_name(logical_name: str, sheet_name: str, single: bool) -> str:
    return logical_name if single else f"{logical_name} · {sheet_name}"


def _workbook_id(file_hash: str, stamp: str, index: int, name: str = "") -> str:
    return "wb_" + hashlib.sha256(f"{file_hash}|{stamp}|{index}|{name}".encode()).hexdigest()[:8]


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
