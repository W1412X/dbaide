"""Read a CSV/Excel file into clean rectangular sheets.

Beyond P1 (header on row 1), this locates the real table inside a sheet: it skips
title/preamble rows above the header, fills vertically-merged grouping columns downward,
and matches the columns under the chosen header. The header row can be detected
automatically (type-coherence) or supplied by the user.

Out of scope (these fall back to "header = first non-empty row"): multi-level / merged
*headers*, crosstab unpivot, multiple tables per sheet, transpose.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CSV_EXTS = {".csv", ".tsv", ".txt"}
_XLSX_EXTS = {".xlsx", ".xlsm"}
SUPPORTED_EXTS = _CSV_EXTS | _XLSX_EXTS

# coarse cell types used for header detection
_EMPTY, _NUM, _DATE, _TEXT = "e", "n", "d", "t"
_NUM_RE = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_DETECT_ROW_CAP = 30   # only the top rows can be preamble; cap the header search
_BODY_WINDOW = 8       # rows of "body" inspected when testing a header candidate


@dataclass
class RawSheet:
    name: str                       # sheet title (or CSV file stem)
    header: list[str]               # header cells (text)
    rows: list[list[Any]]           # data rows, each normalized to len(header)
    header_row: int                 # 0-based index of the header row used
    data_bbox: tuple[int, int, int, int]   # (r0, c0, r1, c1) of the located table
    filled_columns: list[int] = field(default_factory=list)   # cols filled from vertical merges


@dataclass
class RawWorkbook:
    filename: str
    sheets: list[RawSheet]


@dataclass
class RawGrid:
    cells: list[list[Any]]
    merges: list[tuple[int, int, int, int]]   # (r0,c0,r1,c1), 0-based, inclusive
    nrows: int
    ncols: int


@dataclass
class SheetGrid:
    """Filled grid + auto-detected header, for the UI preview / header picker."""
    name: str
    cells: list[list[Any]]
    auto_header_row: int
    filled_columns: list[int]


def read_workbook(path: Path, *, header_overrides: dict[str, int] | None = None) -> RawWorkbook:
    """Read a file into shaped sheets. ``header_overrides`` maps a sheet name to a 0-based
    header row chosen by the user (else the header is auto-detected)."""
    path = Path(path)
    overrides = header_overrides or {}
    sheets: list[RawSheet] = []
    for name, grid in _build_grids(path):
        filled = _fill_vertical_merges(grid)
        sheet = _shape_sheet(name, grid, filled, override=overrides.get(name))
        if sheet is not None:
            sheets.append(sheet)
    if not sheets:
        raise ValueError(f"no non-empty sheet found in {path.name}")
    return RawWorkbook(filename=path.name, sheets=sheets)


def read_sheet_grids(path: Path) -> list[SheetGrid]:
    """Merge-filled grids + auto-detected header per sheet — feeds the preview/header picker."""
    out: list[SheetGrid] = []
    for name, grid in _build_grids(Path(path)):
        filled = _fill_vertical_merges(grid)
        loc = locate_table(grid)
        out.append(SheetGrid(
            name=name, cells=grid.cells, auto_header_row=(loc[0] if loc else 0), filled_columns=filled
        ))
    return out


# ── shaping a located table into a RawSheet ──────────────────────────────────

def _shape_sheet(name: str, grid: RawGrid, filled: list[int], *, override: int | None) -> RawSheet | None:
    loc = locate_table(grid, header_row=override)
    if loc is None:
        return None
    h, c0, c1, last = loc
    header = [_cell_text(grid.cells[h][c]) for c in range(c0, c1 + 1)]
    rows = [
        [grid.cells[r][c] for c in range(c0, c1 + 1)]
        for r in range(h + 1, last + 1)
        if _row_nonempty(grid.cells[r])
    ]
    return RawSheet(
        name=name, header=header, rows=rows, header_row=h,
        data_bbox=(h, c0, last, c1),
        filled_columns=[c for c in filled if c0 <= c <= c1],
    )


# ── header / table location ──────────────────────────────────────────────────

def locate_table(grid: RawGrid, *, header_row: int | None = None) -> tuple[int, int, int, int] | None:
    """Locate the table as (header_row, c0, c1, last_row). With ``header_row`` given, that row
    is used as the header and the columns below it are matched automatically; otherwise the
    header row is detected. Returns None for an entirely empty sheet."""
    cells, nrows, ncols = grid.cells, grid.nrows, grid.ncols
    nonempty = [r for r in range(nrows) if _row_nonempty(cells[r])]
    if not nonempty:
        return None
    if header_row is None:
        h = _detect_header(cells, nonempty, ncols)
    else:
        h = max(0, min(header_row, nrows - 1))
    body = [r for r in nonempty if r > h]
    span_rows = [h, *body]
    cols = [c for r in span_rows for c in range(ncols) if _nonempty(cells[r][c])]
    if not cols:
        return None
    return h, min(cols), max(cols), (body[-1] if body else h)


def _detect_header(cells: list[list[Any]], nonempty: list[int], ncols: int) -> int:
    """First row that is text-dominant and sits directly above type-coherent data columns.
    Falls back to the first non-empty row (P1) when nothing qualifies (e.g. all-text table)."""
    for h in nonempty[:_DETECT_ROW_CAP]:
        below = [r for r in nonempty if r > h][:_BODY_WINDOW]
        if below and _is_header_row(cells, h, below, ncols):
            return h
    return nonempty[0]


def _is_header_row(cells: list[list[Any]], h: int, below: list[int], ncols: int) -> bool:
    # text-dominant label row + at least one column where a text header sits above data
    # whose first value is numeric/date and stays mostly numeric/date.
    transition = False
    for c in range(ncols):
        if _ctype(cells[h][c]) != _TEXT:
            continue
        col = [_ctype(cells[r][c]) for r in below if _nonempty(cells[r][c])]
        if col and col[0] in (_NUM, _DATE) and _ratio(col, (_NUM, _DATE)) >= 0.7:
            transition = True
            break
    if not transition:
        return False
    htypes = [_ctype(cells[h][c]) for c in range(ncols) if _nonempty(cells[h][c])]
    return bool(htypes) and _ratio(htypes, (_TEXT,)) >= 0.5


def _ratio(types: list[str], wanted: tuple[str, ...]) -> float:
    return sum(t in wanted for t in types) / len(types)


def _ctype(v: Any) -> str:
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return _EMPTY
    if isinstance(v, bool):
        return _NUM
    if isinstance(v, (int, float)):
        return _NUM
    if isinstance(v, (_dt.datetime, _dt.date)):
        return _DATE
    return _NUM if _NUM_RE.match(str(v).strip()) else _TEXT


# ── merged-cell fill (vertical grouping columns only) ────────────────────────

def _fill_vertical_merges(grid: RawGrid) -> list[int]:
    """Fill a vertical merge's top-left value down its rows (the hierarchical grouping case).
    Horizontal / block merges (titles, multi-level headers) are intentionally left alone."""
    filled: set[int] = set()
    for (r0, c0, r1, c1) in grid.merges:
        if c0 != c1 or r1 <= r0:
            continue
        value = grid.cells[r0][c0]
        if value is None or (isinstance(value, str) and value.strip() == ""):
            continue
        for r in range(r0, r1 + 1):
            grid.cells[r][c0] = value
        filled.add(c0)
    return sorted(filled)


# ── building the raw grid from a file ────────────────────────────────────────

def _build_grids(path: Path) -> list[tuple[str, RawGrid]]:
    ext = path.suffix.lower()
    if ext in _CSV_EXTS:
        return [_csv_grid(path)]
    if ext in _XLSX_EXTS:
        return _xlsx_grids(path)
    raise ValueError(f"unsupported file type {ext!r}; supported: {', '.join(sorted(SUPPORTED_EXTS))}")


def _csv_grid(path: Path) -> tuple[str, RawGrid]:
    text = _decode(path.read_bytes())
    sample = text[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    ncols = max((len(r) for r in rows), default=0)
    cells = [[(r[c] if c < len(r) else None) for c in range(ncols)] for r in rows]
    return path.stem, RawGrid(cells=cells, merges=[], nrows=len(cells), ncols=ncols)


def _xlsx_grids(path: Path) -> list[tuple[str, RawGrid]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "reading .xlsx/.xlsm requires openpyxl — install it with: pip install openpyxl"
        ) from exc
    # Not read_only: read_only worksheets don't expose merged-cell ranges, which we need.
    wb = load_workbook(path, read_only=False, data_only=True)
    try:
        grids: list[tuple[str, RawGrid]] = []
        for ws in wb.worksheets:
            if ws.sheet_state != "visible":
                continue
            nrows, ncols = ws.max_row or 0, ws.max_column or 0
            cells = [[ws.cell(row=r + 1, column=c + 1).value for c in range(ncols)] for r in range(nrows)]
            merges = [
                (rng.min_row - 1, rng.min_col - 1, rng.max_row - 1, rng.max_col - 1)
                for rng in ws.merged_cells.ranges
            ]
            grids.append((ws.title, RawGrid(cells=cells, merges=merges, nrows=nrows, ncols=ncols)))
        return grids
    finally:
        wb.close()


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _row_nonempty(row: list[Any]) -> bool:
    return any(_nonempty(c) for c in (row or []))


def _nonempty(cell: Any) -> bool:
    return cell is not None and str(cell).strip() != ""


def _cell_text(cell: Any) -> str:
    return "" if cell is None else str(cell).strip()
