"""Unit tests for the process-wide SQL cost governor (FIFO admission control)."""

from __future__ import annotations

import threading
import time

import pytest

from dbaide.core.sql_governor import Cancelled, CostExceeded, CostGovernor


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_disabled_budget_tracks_but_never_gates():
    g = CostGovernor()
    assert g.enabled is False
    tok = g.acquire("SELECT 1", 999_999)              # 0 budget → no reject/queue, but tracked
    assert tok is not None
    snap = g.snapshot()
    assert snap["enabled"] is False
    assert snap["running_count"] == 1 and snap["queued_count"] == 0   # monitor, not gated
    g.release(tok)
    assert g.snapshot()["running_count"] == 0


def test_per_query_cost_over_budget_is_rejected():
    g = CostGovernor()
    g.configure(100)
    with pytest.raises(CostExceeded):
        g.acquire("SELECT * FROM huge", 101)          # can never fit → reject, don't queue
    assert g.snapshot()["queued_count"] == 0          # nothing left dangling in the queue


def test_admit_within_budget_and_release():
    g = CostGovernor()
    g.configure(100)
    tok = g.acquire("SELECT a", 60, connection="shop")
    assert tok is not None
    snap = g.snapshot()
    assert snap["running_count"] == 1 and snap["in_flight_cost"] == 60
    assert snap["running"][0]["cost"] == 60 and snap["running"][0]["connection"] == "shop"
    g.release(tok)
    assert g.snapshot()["in_flight_cost"] == 0


def test_sum_over_budget_queues_until_release():
    g = CostGovernor()
    g.configure(100)
    t1 = g.acquire("a", 60)                            # 60/100 used
    admitted = threading.Event()

    def worker():
        tok = g.acquire("b", 60)                       # 60+60 > 100 → must wait
        admitted.set()
        g.release(tok)

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    assert _wait_for(lambda: g.snapshot()["queued_count"] == 1)
    assert not admitted.is_set()                       # blocked while a holds 60
    g.release(t1)                                      # frees budget
    assert _wait_for(admitted.is_set)                  # b now admitted
    th.join(timeout=2)


def test_strict_fifo_order():
    g = CostGovernor()
    g.configure(10)                                    # only one cost-10 query at a time
    head = g.acquire("head", 10)
    order: list[str] = []
    lock = threading.Lock()
    admitted = {n: threading.Event() for n in ("B", "C")}
    release_gate = {n: threading.Event() for n in ("B", "C")}   # the TEST controls these

    def worker(name):
        tok = g.acquire(name, 10)
        with lock:
            order.append(name)
        admitted[name].set()
        release_gate[name].wait(2.0)                   # hold the slot until the test frees it
        g.release(tok)

    tb = threading.Thread(target=worker, args=("B",), daemon=True)
    tb.start()
    assert _wait_for(lambda: any(q["label"] == "B" for q in g.snapshot()["queued"]))
    tc = threading.Thread(target=worker, args=("C",), daemon=True)
    tc.start()
    assert _wait_for(lambda: g.snapshot()["queued_count"] == 2)   # B enqueued before C

    g.release(head)                                    # B (the head) must go first
    assert _wait_for(admitted["B"].is_set)
    assert order == ["B"] and not admitted["C"].is_set()   # C blocked behind B (strict FIFO)
    release_gate["B"].set()                             # let B finish + release its slot
    assert _wait_for(admitted["C"].is_set)             # only now does C get in
    assert order == ["B", "C"]
    release_gate["C"].set()
    tb.join(timeout=2); tc.join(timeout=2)


def test_cancel_while_queued_leaves_the_queue():
    g = CostGovernor()
    g.configure(10)
    held = g.acquire("hold", 10)                        # fills the budget
    cancel_flag = threading.Event()
    raised = threading.Event()

    def worker():
        try:
            g.acquire("victim", 10, cancel=cancel_flag.is_set)
        except Cancelled:
            raised.set()

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    assert _wait_for(lambda: g.snapshot()["queued_count"] == 1)
    cancel_flag.set()                                  # ask the waiter to give up
    assert _wait_for(raised.is_set)
    assert g.snapshot()["queued_count"] == 0           # removed itself from the queue
    g.release(held)
    th.join(timeout=2)


def test_querytools_execute_respects_the_singleton_governor(tmp_path, monkeypatch):
    """The QueryTools.execute_sql path admits through the process-wide governor."""
    import sqlite3

    from dbaide.adapters import build_adapter
    from dbaide.context.disclosure import DisclosureContext
    from dbaide.core.sql_governor import governor
    from dbaide.models import ConnectionConfig
    from dbaide.tools import QueryTools

    db = tmp_path / "g.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(x)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(5)])
    con.commit(); con.close()
    qt = QueryTools(build_adapter(ConnectionConfig(name="g", type="sqlite", path=str(db))),
                    DisclosureContext())
    # SQLite gives no EXPLAIN estimate, so drive the cost deterministically.
    monkeypatch.setattr(qt, "estimate_rows", lambda sql, database="": 50)
    try:
        governor.configure(100)
        res = qt.execute_sql("SELECT * FROM t")           # cost 50 ≤ 100 → admitted
        assert res.row_count == 5
        assert governor.snapshot()["in_flight_cost"] == 0  # released after completion

        monkeypatch.setattr(qt, "estimate_rows", lambda sql, database="": 500)
        with pytest.raises(CostExceeded):                  # cost 500 > 100 → rejected
            qt.execute_sql("SELECT * FROM t")
    finally:
        governor.configure(0)   # reset the singleton so other tests run ungoverned


def test_lowered_budget_never_deadlocks_a_queued_query():
    g = CostGovernor()
    g.configure(100)
    a = g.acquire("a", 30)                              # 30 in-flight
    admitted = threading.Event()

    def worker():
        tok = g.acquire("b", 80)                        # fits 100, not 30+80
        admitted.set()
        g.release(tok)

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    assert _wait_for(lambda: g.snapshot()["queued_count"] == 1)
    g.configure(50)                                    # now b's cost (80) > budget (50)
    assert not admitted.is_set()                       # still blocked while a runs
    g.release(a)                                        # in-flight → 0: b runs alone
    assert _wait_for(admitted.is_set)                  # admitted despite cost > budget
    th.join(timeout=2)
