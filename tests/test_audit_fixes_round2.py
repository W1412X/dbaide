"""Regression tests for the second audit pass (codex-informed):

- SchemaGuard disclosure-boundary bypass via SQL comments (CRITICAL security)
- SchemaGuard case-insensitive matching (false-rejection fix)
- SQLGuard forbidden-keyword false positives (REPLACE() / TRUNCATE() / t.call)
- outer_limit_value recognizing FETCH FIRST … ROWS ONLY
- JoinCatalogStore path-traversal containment
- LLM client robustness: non-dict response, Retry-After, status-code classification
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbaide.adapters.base import append_limit, outer_limit_value
from dbaide.joins.catalog import JoinCatalogStore
from dbaide.validation import SQLGuard, TableScopeGuard


# ── TableScopeGuard: comment can't smuggle an out-of-scope table (security) ────

@pytest.fixture
def scope_guard():
    # Allowed scope = the orders table only; anything else is out of scope.
    return TableScopeGuard(allow=["public.orders", "orders"])


@pytest.mark.parametrize("sql", [
    "SELECT * FROM /*x*/ secret_table",
    "SELECT * FROM/**/secret_table",
    "SELECT * FROM -- c\n secret_table",
    "SELECT * FROM secret_table",
    "SELECT * FROM public.secret_table",
])
def test_comment_cannot_hide_out_of_scope_table(scope_guard, sql):
    result = scope_guard.validate(sql)
    assert not result.ok, f"scope bypass: {sql!r}"
    assert any(i.code in ("TABLE_OUT_OF_SCOPE", "TABLE_DENIED") for i in result.issues)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders",
    "SELECT * FROM Orders",            # case-insensitive
    "SELECT * FROM PUBLIC.ORDERS",
    "SELECT * FROM /*c*/ orders",      # comment around an in-scope table is fine
    "SELECT * FROM orders WHERE note = 'from secret_table'",  # string literal, not a ref
])
def test_in_scope_table_passes(scope_guard, sql):
    result = scope_guard.validate(sql)
    assert result.ok, f"false rejection: {sql!r} -> {[i.message for i in result.issues]}"


# ── SQLGuard: forbidden-keyword false positives ──────────────────────────────

@pytest.mark.parametrize("sql", [
    "SELECT REPLACE(name, 'a', 'b') FROM t",   # REPLACE() string function
    "SELECT TRUNCATE(x, 2) FROM t",            # TRUNCATE() math function
    "SELECT t.call FROM t",                     # qualified identifier
])
def test_forbidden_keyword_allows_functions_and_qualified_names(sql):
    assert SQLGuard().validate(sql).ok, f"wrongly rejected: {sql!r}"


@pytest.mark.parametrize("sql", [
    "DELETE FROM t",
    "REPLACE INTO t VALUES (1)",                # MySQL REPLACE *statement* still blocked
    "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",  # data-modifying CTE
])
def test_forbidden_keyword_still_blocks_dml(sql):
    assert not SQLGuard().validate(sql).ok, f"NOT blocked: {sql!r}"


# ── outer_limit_value: FETCH FIRST ───────────────────────────────────────────

def test_fetch_first_recognized():
    assert outer_limit_value("SELECT * FROM t FETCH FIRST 10 ROWS ONLY") == 10
    assert outer_limit_value("SELECT * FROM t FETCH NEXT 5 ROWS ONLY") == 5
    assert outer_limit_value("SELECT * FROM t FETCH FIRST ROW ONLY") == 1
    # subquery FETCH is not a top-level limiter
    assert outer_limit_value("SELECT * FROM (SELECT * FROM t FETCH FIRST 5 ROWS ONLY) x") is None


def test_append_limit_does_not_double_fetch_first():
    sql = "SELECT * FROM t FETCH FIRST 10 ROWS ONLY"
    assert append_limit(sql, 100) == sql            # no spurious LIMIT appended
    assert append_limit("SELECT * FROM t", 100) == "SELECT * FROM t LIMIT 100"


# ── JoinCatalogStore: path traversal ─────────────────────────────────────────

@pytest.mark.parametrize("bad", ["..", ".", "../..", "a/../b", "..\\..", "..."])
def test_instance_path_contains_traversal(tmp_path, bad):
    store = JoinCatalogStore(base_dir=tmp_path)
    resolved = store.instance_path(bad).resolve()
    instances = (tmp_path / "instances").resolve()
    assert str(resolved).startswith(str(instances)), f"escaped instances/: {bad!r} -> {resolved}"


def test_instance_path_normal_name(tmp_path):
    store = JoinCatalogStore(base_dir=tmp_path)
    assert store.instance_path("shop").relative_to(tmp_path) == Path("instances/shop/joins.json")


# ── LLM client robustness ────────────────────────────────────────────────────

def _client():
    from dbaide.llm import OpenAICompatibleClient
    from dbaide.models import ModelConfig
    return OpenAICompatibleClient(ModelConfig(
        name="m", base_url="https://example.com/v1", api_key="k", model="x",
    ))


def test_retry_delay_honors_retry_after_seconds():
    import urllib.error
    client = _client()
    exc = urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "7"}, None)
    assert client._retry_delay(0, exc) == 7.0
    # caps absurd values
    exc2 = urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "99999"}, None)
    assert client._retry_delay(0, exc2) == 60.0
    # HTTP-date form falls back to the backoff tuple
    exc3 = urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "Wed, 21 Oct 2025 07:28:00 GMT"}, None)
    assert client._retry_delay(0, exc3) == float(client.RETRY_BACKOFF[0])
    # no header → backoff
    assert client._retry_delay(1) == float(client.RETRY_BACKOFF[1])


def test_classify_prefers_status_code():
    from dbaide.llm_errors import classify_llm_error
    exc = RuntimeError("LLM HTTP 503: upstream had 200 rows cached")
    exc.status_code = 503  # type: ignore[attr-defined]
    err = classify_llm_error(exc)
    assert err.retryable is True
    assert err.evidence.get("status_code") == 503

    auth = RuntimeError("LLM HTTP 401: nope")
    auth.status_code = 401  # type: ignore[attr-defined]
    assert classify_llm_error(auth).retryable is False
