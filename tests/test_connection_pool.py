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


def test_close_all_during_in_use_discards_stale_connection_on_release():
    """close_all() while a connection is checked out must not corrupt accounting:
    the in-use conn belongs to the old epoch, so on release it is closed (not pooled)
    and _total stays consistent — otherwise a later acquire could exceed max_size."""
    reset_registry()
    created = []

    def factory():
        conn = FakeConnection(len(created))
        created.append(conn)
        return conn

    from dbaide.db.connection_pool import ConnectionPool, PoolKey as _PK
    pool = ConnectionPool(key=_PK("shop", "mysql", "main"), max_size=1, factory=factory)

    handle = pool.acquire()           # checks out conn #0 (epoch 0), _total == 1
    assert pool._total == 1
    pool.close_all()                  # epoch -> 1, _total reset to 0, idle cleared
    assert pool._total == 0
    handle.close()                    # released against dead epoch → discarded, not pooled
    assert created[0].closed is True
    assert pool._idle == []           # stale conn was NOT re-pooled
    assert pool._total == 0           # and _total did not go negative

    # The pool is still usable and still honours max_size afterwards.
    with pool.acquire() as conn:
        assert conn.name == 1
        assert pool._total == 1


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


def test_connection_pool_validator_exception_closes_once():
    """When the validator raises on re-acquire from idle, the connection must
    be closed exactly once (not double-closed by both ``_valid`` and the
    caller in ``acquire``)."""
    reset_registry()
    close_count = 0

    class TrackingConn:
        def __init__(self):
            self.closed = False

        def close(self):
            nonlocal close_count
            if self.closed:
                raise RuntimeError("double close!")
            self.closed = True
            close_count += 1

        def rollback(self):
            pass

    created = []

    def factory():
        conn = TrackingConn()
        created.append(conn)
        return conn

    call_count = 0

    def bad_validator(_conn):
        nonlocal call_count
        call_count += 1
        # First two calls succeed (release + acquire-from-idle), third raises
        if call_count >= 3:
            raise RuntimeError("validator boom")
        return True

    pool = for_key(PoolKey("shop", "mysql", "main_vc"), max_size=2, factory=factory)
    pool._validator = bad_validator

    # Round 1: acquire from factory (no validator), release (validator ok → idle)
    with pool.acquire() as conn:
        pass
    assert call_count == 1  # release validated

    # Round 2: acquire from idle (validator ok), release (validator raises)
    with pool.acquire() as conn:
        pass
    assert call_count == 3  # acquire validated (2), release raised (3)

    # The connection should be closed exactly once (not double-closed)
    assert created[0].closed is True
    assert close_count == 1
