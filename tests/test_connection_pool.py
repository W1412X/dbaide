import threading
import time

from dbaide.db.connection_pool import PoolKey, for_key, reset_registry


class FakeConnection:
    def __init__(self, name: int) -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def rollback(self) -> None:
        return None


def test_connection_pool_reuses_idle_connections():
    reset_registry()
    created = []

    def factory():
        conn = FakeConnection(len(created))
        created.append(conn)
        return conn

    pool = for_key(PoolKey("shop", "mysql", "main"), max_size=2, factory=factory)
    with pool.acquire() as first:
        first_name = first.name
    with pool.acquire() as second:
        assert second.name == first_name

    assert len(created) == 1


def test_connection_pool_separates_session_timezones():
    reset_registry()
    created = []

    def factory():
        conn = FakeConnection(len(created))
        created.append(conn)
        return conn

    utc_pool = for_key(PoolKey("shop", "mysql", "main", "+00:00"), max_size=2, factory=factory)
    shanghai_pool = for_key(PoolKey("shop", "mysql", "main", "+08:00"), max_size=2, factory=factory)

    assert utc_pool is not shanghai_pool


def test_connection_pool_caps_concurrent_physical_connections():
    reset_registry()
    created = []
    started = threading.Event()
    release_first = threading.Event()
    acquired = []

    def factory():
        conn = FakeConnection(len(created))
        created.append(conn)
        return conn

    pool = for_key(PoolKey("shop", "mysql", "main"), max_size=1, factory=factory)

    def first_worker():
        with pool.acquire() as conn:
            acquired.append(conn.name)
            started.set()
            release_first.wait(1)

    thread = threading.Thread(target=first_worker)
    thread.start()
    started.wait(1)

    second_done = threading.Event()

    def second_worker():
        with pool.acquire() as conn:
            acquired.append(conn.name)
        second_done.set()

    second = threading.Thread(target=second_worker)
    second.start()
    time.sleep(0.02)
    assert not second_done.is_set()
    release_first.set()
    thread.join()
    second.join()

    assert acquired == [0, 0]
    assert len(created) == 1
