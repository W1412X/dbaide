"""Read a CSV/Excel file into in-memory sheets.

Phase 1 scope: the common case — a clean rectangular table per sheet, with the header
on the first non-empty row. No multi-level headers, crosstab/unpivot, multi-table-per-
sheet, or raw-grid fallback yet (those are deliberately deferred).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CSV_EXTS = {".csv", ".tsv", ".txt"}
_XLSX_EXTS = {".xlsx", ".xlsm"}
SUPPORTED_EXTS = _CSV_EXTS | _XLSX_EXTS


@dataclass
class RawSheet:
    name: str                 # original sheet title (or CSV file stem)
    header: list[str]         # header cells (raw text; empties become "")
    rows: list[list[Any]]     # data rows, each normalized to len(header)
    header_row: int           # 0-based index of the header row in the source


@dataclass
class RawWorkbook:
    filename: str
    sheets: list[RawSheet]


def read_workbook(path: Path) -> RawWorkbook:
    """Read a file into a workbook of sheets. Raises ValueError for unsupported types
    or a file with no importable (non-empty) sheet."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in _CSV_EXTS:
        sheet = _read_csv(path)
        sheets = [sheet] if sheet is not None else []
    elif ext in _XLSX_EXTS:
        sheets = _read_xlsx(path)
    else:
        raise ValueError(
            f"unsupported file type {ext!r}; supported: {', '.join(sorted(SUPPORTED_EXTS))}"
        )
    if not sheets:
        raise ValueError(f"no non-empty sheet found in {path.name}")
    return RawWorkbook(filename=path.name, sheets=sheets)


# ── CSV ──────────────────────────────────────────────────────────────────────

def _read_csv(path: Path) -> RawSheet | None:
    text = _decode(path.read_bytes())
    sample = text[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    # Normalise line endings, then let csv.reader own line parsing so newlines embedded in
    # quoted fields survive (splitting on lines first would corrupt them).
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return _to_sheet(path.stem, rows)


def _decode(raw: bytes) -> str:
    # utf-8-sig first (strips a BOM); gb18030 covers GBK/GB2312 for Chinese CSVs.
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ── XLSX ─────────────────────────────────────────────────────────────────────

def _read_xlsx(path: Path) -> list[RawSheet]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "reading .xlsx/.xlsm requires openpyxl — install it with: pip install openpyxl"
        ) from exc

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets: list[RawSheet] = []
        for ws in wb.worksheets:
            if ws.sheet_state != "visible":   # skip hidden / very-hidden sheets
                continue
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            sheet = _to_sheet(ws.title, rows)
            if sheet is not None:
                sheets.append(sheet)
        return sheets
    finally:
        wb.close()


# ── shared shaping ───────────────────────────────────────────────────────────

def _to_sheet(name: str, all_rows: list[list[Any]]) -> RawSheet | None:
    """Take the first non-empty row as the header; the rest (minus trailing/blank rows)
    as data, each padded/truncated to the header width."""
    header_idx = next((i for i, row in enumerate(all_rows) if _row_nonempty(row)), None)
    if header_idx is None:
        return None
    header = [_cell_text(c) for c in all_rows[header_idx]]
    width = len(header)
    if width == 0:
        return None
    data = [
        [(list(row) + [None] * width)[i] for i in range(width)]
        for row in all_rows[header_idx + 1:]
        if _row_nonempty(row)
    ]
    return RawSheet(name=str(name or "sheet"), header=header, rows=data, header_row=header_idx)


def _row_nonempty(row: list[Any]) -> bool:
    return any(c is not None and str(c).strip() != "" for c in (row or []))


def _cell_text(cell: Any) -> str:
    return "" if cell is None else str(cell).strip()
