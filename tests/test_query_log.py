"""Query-audit log: concurrent record() must not interleave JSONL lines (the
per-instance logger is shared across the multi-run slots)."""

from __future__ import annotations

import json
import threading

from dbaide.observability.query_log import QueryLog


def test_concurrent_record_writes_wellformed_jsonl(tmp_path):
    log = QueryLog("shop", log_dir=tmp_path, persist=True)
    n_threads, per_thread = 16, 40

    def worker(tid: int) -> None:
        for i in range(per_thread):
            log.record(
                caller="agent", database="main",
                sql=f"SELECT {tid}_{i} FROM orders WHERE note = 'x,y\\n{tid}'",
                elapsed_ms=1.0, row_count=i, status="ok",
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_threads * per_thread
    # Every line must be valid, complete JSON (no interleaving / partial writes).
    for ln in lines:
        obj = json.loads(ln)
        assert obj["caller"] == "agent"
        assert obj["status"] == "ok"


def test_record_returns_entry_and_rings(tmp_path):
    log = QueryLog("x", log_dir=tmp_path, persist=False)
    e = log.record(caller="cli", database="db", sql="SELECT 1", elapsed_ms=2.5, row_count=1)
    assert e.caller == "cli" and e.row_count == 1
    # in-memory ring is populated even when persist is off
    assert log.recent(1)[-1].sql.startswith("SELECT 1")
