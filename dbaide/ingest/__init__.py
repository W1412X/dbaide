"""Import CSV/Excel files into a local SQLite database for analysis.

Public surface:
    import_workbooks(paths, *, dest_dir) -> ImportResult   # do the import + write manifest
    read_workbook(path) -> RawWorkbook                      # low-level file → sheets
    SUPPORTED_EXTS                                          # importable file extensions
"""

from __future__ import annotations

from dbaide.ingest.importer import ImportResult, import_workbooks
from dbaide.ingest.manifest import ColumnInfo, ImportManifest, SheetInfo, WorkbookInfo
from dbaide.ingest.readers import SUPPORTED_EXTS, RawSheet, RawWorkbook, read_workbook

__all__ = [
    "import_workbooks",
    "ImportResult",
    "read_workbook",
    "RawWorkbook",
    "RawSheet",
    "SUPPORTED_EXTS",
    "ImportManifest",
    "WorkbookInfo",
    "SheetInfo",
    "ColumnInfo",
]
