"""Structural diff between two catalog snapshots (stored base vs a fresh live
projection), used by schema-tree refresh to decide what changed and how to react.

Pure functions over the normalized shape ``{database: {table: table_doc}}`` where a
``table_doc`` has at least ``columns``/``indexes``/``foreign_keys`` (the structural,
catalog-derivable fields). No I/O — easy to unit-test; the service feeds it the
stored docs and the live projection and applies the resulting strategies.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# Normalized snapshot: {database: {table: table_doc}}
Snapshot = dict[str, dict[str, dict[str, Any]]]


def _columns(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return list(doc.get("columns") or [])


def column_names(doc: dict[str, Any]) -> set[str]:
    return {str(c.get("name") or "") for c in _columns(doc) if c.get("name")}


def table_fingerprint(doc: dict[str, Any]) -> str:
    """A stable hash of a table's STRUCTURE only (columns + keys + indexes + FKs).

    Enrichment (description, samples, profiles) and user notes are deliberately
    excluded: the fingerprint changes only when the live catalog shape changes, so
    enrichment is invalidated exactly when it has actually gone stale."""
    cols = [
        [str(c.get("name") or ""), str(c.get("data_type") or ""),
         bool(c.get("primary_key")), bool(c.get("nullable"))]
        for c in _columns(doc)
    ]
    cols.sort()
    indexes = sorted(
        [str(ix.get("name") or ""), sorted(ix.get("columns") or []), bool(ix.get("unique"))]
        for ix in (doc.get("indexes") or [])
    )
    fks = sorted(
        [str(fk.get("column") or ""), str(fk.get("ref_table") or ""), str(fk.get("ref_column") or "")]
        for fk in (doc.get("foreign_keys") or [])
    )
    comment = str(doc.get("source_comment") or "")
    blob = json.dumps([cols, indexes, fks, comment], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class CatalogDiff:
    added_dbs: list[str] = field(default_factory=list)
    removed_dbs: list[str] = field(default_factory=list)
    added_tables: list[tuple[str, str]] = field(default_factory=list)
    removed_tables: list[tuple[str, str]] = field(default_factory=list)
    changed_tables: list[tuple[str, str]] = field(default_factory=list)   # structure changed
    added_columns: list[tuple[str, str, str]] = field(default_factory=list)
    removed_columns: list[tuple[str, str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.added_dbs or self.removed_dbs or self.added_tables
                    or self.removed_tables or self.changed_tables)

    def summary(self) -> str:
        parts = []
        if self.added_dbs:       parts.append(f"+{len(self.added_dbs)} db")
        if self.removed_dbs:     parts.append(f"-{len(self.removed_dbs)} db")
        if self.added_tables:    parts.append(f"+{len(self.added_tables)} table")
        if self.removed_tables:  parts.append(f"-{len(self.removed_tables)} table")
        if self.changed_tables:  parts.append(f"~{len(self.changed_tables)} table")
        return ", ".join(parts) or "no changes"


def diff_catalog(old: Snapshot, new: Snapshot) -> CatalogDiff:
    """Compute the change set from ``old`` (stored base) to ``new`` (live projection).

    Only databases present in BOTH are compared table-by-table; a database missing
    from ``new`` is reported as removed (the caller must ensure ``new`` only omits a
    database when it is genuinely gone — never when it was merely unreachable)."""
    d = CatalogDiff()
    old_dbs, new_dbs = set(old), set(new)
    d.added_dbs = sorted(new_dbs - old_dbs)
    d.removed_dbs = sorted(old_dbs - new_dbs)

    for db in sorted(old_dbs & new_dbs):
        old_t, new_t = old[db], new[db]
        old_names, new_names = set(old_t), set(new_t)
        for t in sorted(new_names - old_names):
            d.added_tables.append((db, t))
        for t in sorted(old_names - new_names):
            d.removed_tables.append((db, t))
        for t in sorted(old_names & new_names):
            if table_fingerprint(old_t[t]) == table_fingerprint(new_t[t]):
                continue
            d.changed_tables.append((db, t))
            old_cols, new_cols = column_names(old_t[t]), column_names(new_t[t])
            for c in sorted(new_cols - old_cols):
                d.added_columns.append((db, t, c))
            for c in sorted(old_cols - new_cols):
                d.removed_columns.append((db, t, c))
    return d
