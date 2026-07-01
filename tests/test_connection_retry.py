"""execute_readonly retries ONCE on a dropped/broken connection.

A pooled connection the server closed while idle (idle timeout, pgbouncer, failover, firewall)
fails on first use because the client didn't know it was dead. Read-only queries have no side
effects, so retrying once with a fresh connection self-heals the failure instead of surfacing a
spurious "server closed the connection" to the user. Timeouts and real query errors must NOT be
retried."""

from __future__ import annotations

import sqlite3

import pytest

from dbaide.adapters import build_adapter
from dbaide.models import ConnectionConfig


def _adapter(tmp_path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE t(x INTEGER); INSERT INTO t VALUES (1), (2);")
    con.commit()
    con.close()
    return build_adapter(ConnectionConfig(name="t", type="sqlite", path=str(db)))


def _flaky(adapter, exc: Exception, fail_times: int = 1):
    """Wrap _execute_readonly_impl so the first `fail_times` calls raise `exc`."""
    real = adapter._execute_readonly_impl
    state = {"n": 0}

    def wrapped(sql, **kw):
        state["n"] += 1
        if state["n"] <= fail_times:
            raise exc
        return real(sql, **kw)

    adapter._execute_readonly_impl = wrapped
    return state


def test_retries_once_and_succeeds_on_dropped_connection(tmp_path):
    ad = _adapter(tmp_path)
    state = _flaky(ad, Exception("server closed the connection unexpectedly"), fail_times=1)
    res = ad.execute_readonly("SELECT x FROM t ORDER BY x")
    assert state["n"] == 2                       # failed once, retried, succeeded
    assert res.row_count == 2


def test_retry_is_capped_at_one(tmp_path):
    ad = _adapter(tmp_path)
    state = _flaky(ad, Exception("connection reset by peer"), fail_times=99)
    with pytest.raises(Exception, match="connection reset by peer"):
        ad.execute_readonly("SELECT x FROM t")
    assert state["n"] == 2                        # one original + exactly one retry, then gives up


def test_no_retry_on_query_error(tmp_path):
    ad = _adapter(tmp_path)
    state = _flaky(ad, Exception("near \"SELCT\": syntax error"), fail_times=99)
    with pytest.raises(Exception, match="syntax error"):
        ad.execute_readonly("SELCT 1")
    assert state["n"] == 1                        # a real query error is not a connection drop


def test_no_retry_on_statement_timeout(tmp_path):
    ad = _adapter(tmp_path)
    # A timeout is a driver "operational" error too, but retrying re-runs the slow query.
    state = _flaky(ad, Exception("canceling statement due to statement timeout"), fail_times=99)
    with pytest.raises(Exception, match="statement timeout"):
        ad.execute_readonly("SELECT x FROM t")
    assert state["n"] == 1


def test_no_retry_on_mysql_max_execution_time(tmp_path):
    ad = _adapter(tmp_path)
    state = _flaky(
        ad,
        Exception("(3024, 'Query execution was interrupted, maximum statement execution time exceeded')"),
        fail_times=99,
    )
    with pytest.raises(Exception):
        ad.execute_readonly("SELECT x FROM t")
    assert state["n"] == 1


@pytest.mark.parametrize(
    "message,retryable",
    [
        ("server closed the connection unexpectedly", True),
        ("connection already closed", True),
        ("MySQL server has gone away", True),
        ("Lost connection to MySQL server during query", True),
        ("broken pipe", True),
        ("SSL connection has been closed unexpectedly", True),
        ("terminating connection due to administrator command", True),
        ("canceling statement due to statement timeout", False),
        ("maximum statement execution time exceeded", False),
        ("syntax error at or near \"FROM\"", False),
        ("permission denied for table users", False),
        ("division by zero", False),
    ],
)
def test_connection_error_classification(tmp_path, message, retryable):
    ad = _adapter(tmp_path)
    assert ad._is_connection_error(Exception(message)) is retryable


def test_postgres_classifies_by_exception_type():
    import psycopg
    from dbaide.adapters.postgres import PostgresAdapter
    from dbaide.models import ConnectionConfig
    ad = PostgresAdapter(ConnectionConfig(name="p", type="postgres", host="h", database="d"))
    # a real dropped connection (OperationalError) → retry
    assert ad._is_connection_error(psycopg.OperationalError("server closed the connection")) is True
    assert ad._is_connection_error(psycopg.InterfaceError("connection is closed")) is True
    # statement_timeout (QueryCanceled, a subclass of OperationalError) → do NOT retry
    assert ad._is_connection_error(psycopg.errors.QueryCanceled("canceling statement")) is False
    # a plain programming error → not a connection drop
    assert ad._is_connection_error(psycopg.ProgrammingError("syntax error")) is False


def test_mysql_classifies_by_error_code():
    import pymysql
    from dbaide.adapters.mysql import MySQLAdapter
    from dbaide.models import ConnectionConfig
    ad = MySQLAdapter(ConnectionConfig(name="m", type="mysql", host="h", database="d"))
    assert ad._is_connection_error(pymysql.err.OperationalError(2006, "MySQL server has gone away")) is True
    assert ad._is_connection_error(pymysql.err.OperationalError(2013, "Lost connection")) is True
    assert ad._is_connection_error(pymysql.err.InterfaceError(0, "")) is True
    # 3024 = query timeout → do NOT retry
    assert ad._is_connection_error(pymysql.err.OperationalError(3024, "max execution time exceeded")) is False
    # a data/programming error → not a connection drop
    assert ad._is_connection_error(pymysql.err.ProgrammingError(1064, "syntax error")) is False
