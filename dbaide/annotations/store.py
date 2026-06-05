"""User annotations (notes) on database/table/column objects, per connection.

These are *authoritative* user notes that override DB-native comments and any
schema inference. They live in their OWN store — NOT in the asset tree under
``~/.dbaide/assets`` — because assets are rebuilt by ``AssetBuilder`` and a
rebuild would wipe anything written there. Notes are merged back into the
disclosed schema at query time.

Mirrors the shape of ``dbaide.joins.catalog.JoinCatalogStore`` (per-instance
JSON file, atomic save, upsert-by-key CRUD).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("dbaide.annotations")

DEFAULT_ANNOTATION_DIR = Path.home() / ".dbaide" / "annotations"
SCOPES = ("database", "table", "column")


def _norm(value: str) -> str:
    return str(value or "").strip().lower()


def annotation_key(scope: str, database: str, table: str, column: str) -> tuple[str, str, str, str]:
    return (_norm(scope), _norm(database), _norm(table), _norm(column))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_record(
    *,
    scope: str,
    note: str,
    database: str = "",
    table: str = "",
    column: str = "",
    source: str = "user",
    ann_id: str = "",
) -> dict[str, Any]:
    scope = _norm(scope)
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    if scope in ("table", "column") and not str(table).strip():
        raise ValueError(f"scope={scope} requires a table name")
    if scope == "column" and not str(column).strip():
        raise ValueError("scope=column requires a column name")
    now = _utc_now()
    return {
        "id": ann_id or uuid.uuid4().hex[:12],
        "scope": scope,
        "database": str(database or "").strip(),
        "table": str(table or "").strip() if scope != "database" else "",
        "column": str(column or "").strip() if scope == "column" else "",
        "note": str(note or "").strip(),
        "source": str(source or "user").strip() or "user",
        "created_at": now,
        "updated_at": now,
    }


def apply_notes_to_doc(store: "AnnotationStore", instance: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Fold user notes into an asset doc in place (sets ``user_note`` fields).

    Storage stays separate from the asset (a rebuild never overwrites notes); this
    merges them for display. Handles table / database / instance docs."""
    if not isinstance(doc, dict):
        return doc
    kind = str(doc.get("kind") or "")
    try:
        if kind == "table":
            db = str(doc.get("database") or "")
            table = str(doc.get("name") or doc.get("table") or "")
            view = store.annotations_for_tables(instance, [(db, table)])
            key = (db.strip().lower(), table.strip().lower())
            doc["user_note"] = view["tables"].get(key, "")
            col_notes = view["columns"].get(key, {})
            for col in doc.get("columns", []):
                note = col_notes.get(str(col.get("name") or "").strip().lower())
                if note:
                    col["user_note"] = note
        elif kind == "database":
            db = str(doc.get("name") or "")
            records = store.list_records(instance, database=db)
            tnotes: dict[str, str] = {}
            for r in records:
                scope = _norm(r.get("scope"))
                if scope == "database":
                    doc["user_note"] = str(r.get("note") or "")
                elif scope == "table":
                    tnotes[_norm(r.get("table"))] = str(r.get("note") or "")
            for table in doc.get("tables", []):
                note = tnotes.get(str(table.get("name") or "").strip().lower())
                if note:
                    table["user_note"] = note
        else:  # instance
            records = store.list_records(instance)
            dbnotes: dict[str, str] = {}
            for r in records:
                if _norm(r.get("scope")) != "database":
                    continue
                dbnotes[_norm(r.get("database"))] = str(r.get("note") or "")
            if dbnotes.get(""):
                doc["user_note"] = dbnotes[""]
            for db in doc.get("databases", []):
                note = dbnotes.get(str(db.get("name") or "").strip().lower())
                if note:
                    db["user_note"] = note
    except Exception:  # display merge must never break the doc
        return doc
    return doc


class AnnotationStore:
    """CRUD for user notes under ~/.dbaide/annotations/instances/{instance}/."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is not None:
            self.base_dir = Path(base_dir).expanduser()
        else:
            self.base_dir = Path(os.environ.get("DBAIDE_ANNOTATIONS", DEFAULT_ANNOTATION_DIR)).expanduser()

    def instance_path(self, instance: str) -> Path:
        safe = str(instance or "").replace("/", "_").replace("\\", "_").strip() or "default"
        return self.base_dir / "instances" / safe / "annotations.json"

    # -- queries ---------------------------------------------------------

    def list_records(
        self,
        instance: str,
        *,
        scope: str = "",
        database: str = "",
        table: str = "",
        column: str = "",
    ) -> list[dict[str, Any]]:
        records = self._load(instance)
        out: list[dict[str, Any]] = []
        for rec in records:
            if scope and _norm(rec.get("scope")) != _norm(scope):
                continue
            if database and _norm(rec.get("database")) not in {"", _norm(database)}:
                continue
            if table and _norm(rec.get("table")) != _norm(table):
                continue
            if column and _norm(rec.get("column")) != _norm(column):
                continue
            out.append(dict(rec))
        out.sort(key=lambda r: (r.get("scope") or "", r.get("table") or "", r.get("column") or ""))
        return out

    def annotations_for_tables(
        self,
        instance: str,
        tables: list[tuple[str, str]],
    ) -> dict[str, Any]:
        """Structured notes for a set of (database, table) targets.

        Returns ``{"databases": {db: note}, "tables": {(db,table): note},
        "columns": {(db,table): {col: note}}}`` — keys lower-cased for lookup."""
        records = self._load(instance)
        want_tables = {(_norm(db), _norm(tbl)) for db, tbl in tables}
        want_dbs = {db for db, _ in want_tables}
        databases: dict[str, str] = {}
        tnotes: dict[tuple[str, str], str] = {}
        cnotes: dict[tuple[str, str], dict[str, str]] = {}
        for rec in records:
            scope = _norm(rec.get("scope"))
            db = _norm(rec.get("database"))
            tbl = _norm(rec.get("table"))
            note = str(rec.get("note") or "").strip()
            if not note:
                continue
            if scope == "database":
                # An empty-db note (db-agnostic) applies to every target database.
                if db == "" or db in want_dbs:
                    databases[db] = note
            elif scope == "table":
                for wdb, wtbl in want_tables:
                    if wtbl == tbl and (db == "" or db == wdb):
                        tnotes[(wdb, wtbl)] = note
            elif scope == "column":
                col = _norm(rec.get("column"))
                for wdb, wtbl in want_tables:
                    if wtbl == tbl and (db == "" or db == wdb):
                        cnotes.setdefault((wdb, wtbl), {})[col] = note
        return {"databases": databases, "tables": tnotes, "columns": cnotes}

    # -- mutations -------------------------------------------------------

    def add(
        self,
        instance: str,
        *,
        scope: str,
        note: str,
        database: str = "",
        table: str = "",
        column: str = "",
        source: str = "user",
    ) -> dict[str, Any]:
        """Upsert a note keyed by (scope, database, table, column)."""
        record = make_record(
            scope=scope, note=note, database=database, table=table, column=column, source=source
        )
        records = self._load(instance)
        key = annotation_key(record["scope"], record["database"], record["table"], record["column"])
        for index, existing in enumerate(records):
            ex_key = annotation_key(
                existing.get("scope", ""),
                existing.get("database", ""),
                existing.get("table", ""),
                existing.get("column", ""),
            )
            if ex_key == key:
                record["id"] = existing.get("id") or record["id"]
                record["created_at"] = existing.get("created_at") or record["created_at"]
                records[index] = record
                self._save(instance, records)
                return dict(record)
        records.append(record)
        self._save(instance, records)
        return dict(record)

    def update(self, instance: str, ann_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        records = self._load(instance)
        for index, rec in enumerate(records):
            if str(rec.get("id") or "") != str(ann_id):
                continue
            updated = dict(rec)
            if "note" in fields and fields["note"] is not None:
                updated["note"] = str(fields["note"]).strip()
            updated["updated_at"] = _utc_now()
            records[index] = updated
            self._save(instance, records)
            return dict(updated)
        return None

    def delete(
        self,
        instance: str,
        *,
        ann_id: str = "",
        scope: str = "",
        database: str = "",
        table: str = "",
        column: str = "",
    ) -> bool:
        records = self._load(instance)
        if ann_id:
            kept = [r for r in records if str(r.get("id") or "") != str(ann_id)]
        else:
            key = annotation_key(scope, database, table, column)
            kept = [
                r
                for r in records
                if annotation_key(r.get("scope", ""), r.get("database", ""), r.get("table", ""), r.get("column", ""))
                != key
            ]
        if len(kept) == len(records):
            return False
        self._save(instance, kept)
        return True

    # -- persistence -----------------------------------------------------

    def _load(self, instance: str) -> list[dict[str, Any]]:
        path = self.instance_path(instance)
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("annotations_read_failed: %s", exc)
            return []
        items = data.get("annotations") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        return [dict(item) for item in items if isinstance(item, dict)]

    def _save(self, instance: str, records: list[dict[str, Any]]) -> None:
        path = self.instance_path(instance)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "instance": instance,
            "schema_version": 1,
            "updated_at": _utc_now(),
            "annotations": records,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
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
