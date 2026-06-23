"""A managed Excel/CSV collection backing one connection.

An ``ExcelCollection`` owns the ``imports/{conn}/`` directory: the generated ``data.db``
and its ``manifest.json``. It's the single place the CLI and the desktop UI go through to
create a collection, add more workbooks to it, or remove one — the connection itself stays
an ordinary read-only ``sqlite`` connection.

"Is this connection an Excel collection?" is answered by whether its database sits inside an
``imports/{name}/`` directory next to a manifest — see :func:`collection_for_connection`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbaide.ingest.importer import ImportResult, import_workbooks, remove_workbook
from dbaide.ingest.manifest import ImportManifest, WorkbookInfo

IMPORTS_DIRNAME = "imports"
_DB_NAME = "data.db"
_MANIFEST_NAME = "manifest.json"


class ExcelCollection:
    def __init__(self, dest_dir: Path | str) -> None:
        self.dir = Path(dest_dir)

    @property
    def db_path(self) -> Path:
        return self.dir / _DB_NAME

    @property
    def manifest_path(self) -> Path:
        return self.dir / _MANIFEST_NAME

    def exists(self) -> bool:
        return self.manifest_path.exists()

    def load(self) -> ImportManifest:
        return ImportManifest.load(self.manifest_path) if self.exists() else ImportManifest()

    def workbooks(self) -> list[WorkbookInfo]:
        return self.load().workbooks

    def is_empty(self) -> bool:
        return not self.workbooks()

    def add(
        self,
        paths: list[Path | str],
        *,
        on_progress: Callable[[str], None] | None = None,
    ) -> ImportResult:
        """Add one or more files. Creates the collection if it doesn't exist yet, otherwise
        appends to it (keeping table names unique)."""
        return import_workbooks(
            paths, dest_dir=self.dir, append=self.exists(), on_progress=on_progress
        )

    def remove(self, workbook_id: str) -> ImportManifest:
        """Drop a workbook's tables and forget it. Raises KeyError if the id is unknown."""
        return remove_workbook(self.dir, workbook_id)


def imports_root(config_dir: Path | str) -> Path:
    return Path(config_dir) / IMPORTS_DIRNAME


def collection_dir(config_dir: Path | str, conn_name: str) -> Path:
    return imports_root(config_dir) / conn_name


def collection_for_connection(config_dir: Path | str, db_path: str | Path) -> ExcelCollection | None:
    """Return the collection a sqlite connection belongs to, or None if it's an ordinary
    database. Matches connections whose file lives under ``{config_dir}/imports/*/`` next to
    a manifest."""
    if not db_path:
        return None
    path = Path(db_path)
    root = imports_root(config_dir).resolve()
    try:
        parent = path.resolve().parent
        parent.relative_to(root)
    except (ValueError, OSError):
        return None
    collection = ExcelCollection(parent)
    return collection if collection.exists() else None
