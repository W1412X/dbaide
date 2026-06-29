"""Concurrency stress for the SQL cost governor: under a FIXED budget the sum of
running costs must never exceed the budget, and the pool must never deadlock."""

from __future__ import annotations

import random
import threading
import time

from dbaide.core.sql_governor import CostGovernor


def test_budget_is_never_exceeded_and_no_deadlock_under_load():
    g = CostGovernor()
    budget = 1000
    g.configure(budget)

    rng = random.Random(20260629)
    n_workers = 40
    iters = 8
    done = [0]
    done_lock = threading.Lock()
    violations: list[int] = []
    stop = threading.Event()

    def monitor():
        # Every committed state must satisfy sum(running) <= budget for a fixed budget.
        while not stop.is_set():
            snap = g.snapshot()
            if snap["in_flight_cost"] > budget:
                violations.append(snap["in_flight_cost"])
            time.sleep(0.001)

    def worker(seed: int):
        r = random.Random(seed)
        for _ in range(iters):
            cost = r.randint(1, 400)                 # every cost < budget → always fits eventually
            tok = g.acquire("q", cost, connection="c")
            time.sleep(r.uniform(0, 0.003))          # hold the slot briefly
            g.release(tok)
        with done_lock:
            done[0] += 1

    mon = threading.Thread(target=monitor, daemon=True)
    mon.start()
    threads = [threading.Thread(target=worker, args=(rng.random(),), daemon=True)
               for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    stop.set()
    mon.join(timeout=1)

    assert done[0] == n_workers, f"deadlock/hang: only {done[0]}/{n_workers} workers finished"
    assert not violations, f"budget exceeded {len(violations)}x (e.g. {violations[:5]}, budget={budget})"
    final = g.snapshot()
    assert final["in_flight_cost"] == 0 and final["queued_count"] == 0  # fully drained


def test_many_over_budget_queries_all_rejected_fast():
    g = CostGovernor()
    g.configure(100)
    from dbaide.core.sql_governor import CostExceeded

    rejected = [0]

    def worker():
        try:
            g.acquire("big", 500)   # cost > budget → must reject immediately, never block
        except CostExceeded:
            rejected[0] += 1

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)          # if any blocked, join times out and the assert below fails

    assert rejected[0] == 20
    assert g.snapshot()["queued_count"] == 0   # nothing left dangling
