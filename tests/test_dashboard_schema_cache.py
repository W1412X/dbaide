"""The dashboard builder's schema grounding (~60 read queries) is cached per
connection so generating several dashboards in a session doesn't re-introspect, and
is invalidated when the connection changes."""

from __future__ import annotations

import pytest

from dbaide.desktop.service import DesktopService


class _Col:
    def __init__(self, name: str, type: str = "INTEGER") -> None:
        self.name = name
        self.type = type


class _Table:
    def __init__(self, name: str) -> None:
        self.name = name


class _CountingAdapter:
    def __init__(self) -> None:
        self.list_calls = 0
        self.describe_calls = 0

    def list_tables(self):
        self.list_calls += 1
        return [_Table("sales")]

    def describe_table(self, name: str):
        self.describe_calls += 1
        return [_Col("region", "INTEGER"), _Col("amount", "REAL")]


class _Tools:
    def __init__(self, adapter) -> None:
        self.adapter = adapter


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("DBAIDE_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("DBAIDE_BOARDS", str(tmp_path / "boards"))
    return DesktopService()


def test_schema_context_cached_per_connection(service, monkeypatch):
    monkeypatch.setattr(service, "_distinct_values", lambda *a, **k: None)  # no real DB
    adapter = _CountingAdapter()
    tools = _Tools(adapter)

    first = service._dashboard_schema_context(tools, "shop")
    second = service._dashboard_schema_context(tools, "shop")
    assert first == second and "sales(" in first
    # Introspection runs once despite two builds.
    assert adapter.list_calls == 1
    assert adapter.describe_calls == 1

    # A different connection is introspected on its own.
    other = _CountingAdapter()
    service._dashboard_schema_context(_Tools(other), "warehouse")
    assert other.list_calls == 1


def test_save_connection_invalidates_schema_cache(service):
    service._dashboard_schema_cache["shop"] = "stale(...)"
    service.save_connection({"name": "shop", "type": "sqlite", "path": ":memory:"})
    assert "shop" not in service._dashboard_schema_cache


def test_delete_connection_invalidates_schema_cache(service):
    service.save_connection({"name": "gone", "type": "sqlite", "path": ":memory:"})
    service._dashboard_schema_cache["gone"] = "x"
    service.delete_connection({"name": "gone"})
    assert "gone" not in service._dashboard_schema_cache
