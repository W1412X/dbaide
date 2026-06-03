import os
from pathlib import Path

import pytest

from dbaide.history.query_store import QueryHistoryStore, MAX_ENTRIES

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_record_and_recent(tmp_path: Path):
    s = QueryHistoryStore(base_dir=tmp_path)
    s.record("c1", "select 1", ok=True, row_count=1, elapsed_ms=2.0)
    s.record("c1", "select 2", ok=False)
    recent = s.recent("c1")
    assert [e["sql"] for e in recent] == ["select 2", "select 1"]
    assert recent[0]["ok"] is False


def test_collapse_consecutive_duplicate(tmp_path: Path):
    s = QueryHistoryStore(base_dir=tmp_path)
    s.record("c1", "select 1")
    s.record("c1", "select 1")
    assert len(s.recent("c1")) == 1


def test_blank_sql_ignored(tmp_path: Path):
    s = QueryHistoryStore(base_dir=tmp_path)
    s.record("c1", "   ")
    assert s.recent("c1") == []


def test_per_connection_isolation(tmp_path: Path):
    s = QueryHistoryStore(base_dir=tmp_path)
    s.record("a", "select 1")
    s.record("b", "select 2")
    assert [e["sql"] for e in s.recent("a")] == ["select 1"]
    assert [e["sql"] for e in s.recent("b")] == ["select 2"]


def test_cap(tmp_path: Path):
    s = QueryHistoryStore(base_dir=tmp_path)
    for i in range(MAX_ENTRIES + 50):
        s.record("c", f"select {i}")
    assert len(s.recent("c", limit=10_000)) == MAX_ENTRIES


def test_clear(tmp_path: Path):
    s = QueryHistoryStore(base_dir=tmp_path)
    s.record("c", "select 1")
    s.clear("c")
    assert s.recent("c") == []


def test_panel_loads(qapp):
    from dbaide.desktop.views.query_history import QueryHistoryPanel
    p = QueryHistoryPanel()
    p.load([{"sql": "select 1", "ok": True, "row_count": 1, "elapsed_ms": 1.0, "ts": 0}])
    assert p.list.count() == 1
    assert p.stack.currentIndex() == 1
    p.load([])
    assert p.stack.currentIndex() == 0
