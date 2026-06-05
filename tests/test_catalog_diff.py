"""Pure tests for the catalog diff engine (schema-tree refresh foundation)."""

from __future__ import annotations

from dbaide.assets.diff import diff_catalog, table_fingerprint


def _tbl(cols, *, comment="", fks=None, indexes=None):
    return {
        "columns": [{"name": n, "data_type": t, "primary_key": pk, "nullable": nl}
                    for (n, t, pk, nl) in cols],
        "source_comment": comment,
        "foreign_keys": fks or [],
        "indexes": indexes or [],
    }


def test_fingerprint_ignores_enrichment_and_notes():
    base = _tbl([("id", "int", True, False)])
    a = dict(base, description="LLM summary", sample_rows=[{"id": 1}], user_note="x")
    b = dict(base, description="different", sample_rows=[], user_note="y")
    assert table_fingerprint(a) == table_fingerprint(b)  # only structure matters


def test_fingerprint_changes_on_type_and_comment():
    a = _tbl([("paid_at", "int", False, True)])
    assert table_fingerprint(a) != table_fingerprint(_tbl([("paid_at", "bigint", False, True)]))
    assert table_fingerprint(a) != table_fingerprint(_tbl([("paid_at", "int", False, True)], comment="UTC"))


def test_diff_added_removed_db_and_table():
    old = {"shop": {"orders": _tbl([("id", "int", True, False)])}}
    new = {
        "shop": {"orders": _tbl([("id", "int", True, False)]),
                 "users": _tbl([("id", "int", True, False)])},
        "analytics": {"events": _tbl([("id", "int", True, False)])},
    }
    d = diff_catalog(old, new)
    assert d.added_dbs == ["analytics"]
    assert d.removed_dbs == []
    assert ("shop", "users") in d.added_tables
    assert d.removed_tables == []
    assert not d.is_empty()


def test_diff_removed_table_and_db():
    old = {"shop": {"orders": _tbl([("id", "int", True, False)])},
           "old_db": {"t": _tbl([("a", "int", False, True)])}}
    new = {"shop": {}}
    d = diff_catalog(old, new)
    assert d.removed_dbs == ["old_db"]
    assert ("shop", "orders") in d.removed_tables


def test_diff_changed_table_columns():
    old = {"shop": {"orders": _tbl([("id", "int", True, False), ("legacy", "text", False, True)])}}
    new = {"shop": {"orders": _tbl([("id", "int", True, False), ("amount", "real", False, True)])}}
    d = diff_catalog(old, new)
    assert ("shop", "orders") in d.changed_tables
    assert ("shop", "orders", "amount") in d.added_columns
    assert ("shop", "orders", "legacy") in d.removed_columns


def test_diff_unchanged_is_empty():
    snap = {"shop": {"orders": _tbl([("id", "int", True, False)])}}
    d = diff_catalog(snap, {"shop": {"orders": _tbl([("id", "int", True, False)])}})
    assert d.is_empty() and d.summary() == "no changes"
