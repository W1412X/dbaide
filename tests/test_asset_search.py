"""AssetSearch.search result limiting (incl. the non-positive-limit slice guard)."""
from __future__ import annotations

from pathlib import Path

from dbaide.assets.search import AssetSearch


class _StubStore:
    """Minimal AssetStore surface: one database with several name-matching tables."""

    def __init__(self, n_tables: int):
        self._tables = [{"name": f"orders_{i}", "description": "orders data"} for i in range(n_tables)]

    def instance_doc(self, instance, fingerprint=""):
        return None

    def database_docs(self, instance, fingerprint=""):
        return [{"name": "main"}]

    def database_dir(self, instance, db):
        return Path("/nonexistent")

    def _read_optional(self, path):
        return None

    def table_docs(self, instance, db, fingerprint=""):
        return list(self._tables)

    def column_docs(self, instance, db, table, fingerprint=""):
        return []


def _search(n_tables, limit):
    s = AssetSearch.__new__(AssetSearch)
    s.store = _StubStore(n_tables)
    return s.search("orders", instances=["ci"], limit=limit)


def test_search_respects_positive_limit():
    assert len(_search(10, 3)) == 3


def test_search_clamps_nonpositive_limit():
    # A negative limit must NOT become hits[:-N] ("all but last N"); clamp to >=1.
    assert len(_search(10, -5)) == 1
    assert len(_search(10, 0)) == 1
