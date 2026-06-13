"""Tests for the resource-control layer: policy, budget, query log."""

from __future__ import annotations

import threading
import time

import pytest

from dbaide.db import budget as budget_mod
from dbaide.db.budget import QueryBudget
from dbaide.db.policy import (
    DEFAULT_LOAD_PROFILE,
    LOAD_PROFILES,
    ResourcePolicy,
    resolve_policy,
)
from dbaide.observability import query_log
from dbaide.config import ConfigManager
from dbaide.models import ConnectionConfig


class TestResourcePolicy:
    def test_default_is_production(self):
        p = resolve_policy(load_profile=None)
        assert p == LOAD_PROFILES[DEFAULT_LOAD_PROFILE]
        assert p.max_inflight_queries == 16
        assert p.build_max_workers == 4
        assert p.build_profile_mode == "light"

    def test_unknown_profile_falls_back_to_production(self):
        assert resolve_policy(load_profile="bogus") == LOAD_PROFILES["production"]

    def test_dev_is_looser_than_production(self):
        dev = resolve_policy(load_profile="dev")
        prod = resolve_policy(load_profile="production")
        assert dev.max_inflight_queries >= prod.max_inflight_queries
        assert dev.max_row_limit > prod.max_row_limit

    def test_overrides_apply_with_type_coercion(self):
        p = resolve_policy(
            load_profile="production",
            overrides={"max_inflight_queries": "5", "max_row_limit": 250},
        )
        assert p.max_inflight_queries == 5
        assert p.max_row_limit == 250
        # untouched fields keep preset values
        assert p.build_max_workers == 4

    def test_invalid_override_keys_ignored(self):
        p = resolve_policy(load_profile="production", overrides={"nonsense": 9, "max_row_limit": None})
        assert not hasattr(p, "nonsense")
        assert p.max_row_limit == LOAD_PROFILES["production"].max_row_limit

    def test_frozen(self):
        with pytest.raises(Exception):
            ResourcePolicy().max_row_limit = 1  # type: ignore[misc]


class TestQueryBudget:
    def test_never_exceeds_max_inflight(self):
        budget = QueryBudget("t", max_inflight=3)
        observed_peak = 0
        peak_lock = threading.Lock()
        start = threading.Event()

        def worker():
            start.wait()
            with budget.acquire("build"):
                nonlocal observed_peak
                with peak_lock:
                    observed_peak = max(observed_peak, budget.inflight)
                time.sleep(0.02)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join()

        assert observed_peak <= 3
        assert budget.stats.peak_inflight <= 3
        assert budget.stats.total_queries == 20
        assert budget.stats.by_caller["build"] == 20

    def test_registry_rebuilds_on_limit_change(self):
        budget_mod.reset_registry()
        a = budget_mod.for_instance("inst", max_inflight=2)
        a_again = budget_mod.for_instance("inst", max_inflight=2)
        assert a is a_again
        b = budget_mod.for_instance("inst", max_inflight=5)
        assert b is not a
        assert b.max_inflight == 5


class TestQueryLog:
    def test_record_persists_and_buffers(self, tmp_path):
        log = query_log.QueryLog("inst", log_dir=tmp_path, persist=True)
        log.record(caller="build", database="db", sql="SELECT 1", elapsed_ms=1.2, row_count=1)
        log.record(caller="agent", database="db", sql="SELECT 2", elapsed_ms=3.4, row_count=0, status="error", error="boom")

        recent = log.recent()
        assert len(recent) == 2
        assert recent[0].caller == "build"
        assert recent[1].status == "error"

        tailed = log.tail_file(limit=10)
        assert len(tailed) == 2
        assert tailed[-1]["error"] == "boom"

        summary = log.summary()
        assert summary["total"] == 2
        assert summary["errors"] == 1
        assert summary["by_caller"] == {"build": 1, "agent": 1}

    def test_subscribers_notified_and_unsubscribe(self, tmp_path):
        log = query_log.QueryLog("inst", log_dir=tmp_path, persist=False)
        seen: list[str] = []
        unsub = log.subscribe(lambda e: seen.append(e.sql))
        log.record(caller="gui", database="", sql="SELECT 1", elapsed_ms=0, row_count=0)
        unsub()
        log.record(caller="gui", database="", sql="SELECT 2", elapsed_ms=0, row_count=0)
        assert seen == ["SELECT 1"]

    def test_subscriber_exception_does_not_break_record(self, tmp_path):
        log = query_log.QueryLog("inst", log_dir=tmp_path, persist=False)
        log.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("bad")))
        entry = log.record(caller="gui", database="", sql="SELECT 1", elapsed_ms=0, row_count=0)
        assert entry.status == "ok"


class TestConfigIntegration:
    def test_load_profile_roundtrip(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = ConfigManager(path=path)
        cfg.upsert_connection(ConnectionConfig(name="prod", type="sqlite", path="/tmp/x.db", load_profile="dev"))
        reloaded = ConfigManager(path=path)
        assert reloaded.connections()["prod"].load_profile == "dev"

    def test_resource_defaults_roundtrip(self, tmp_path):
        from dbaide.db import policy as policy_mod
        policy_mod.clear_cache()
        path = tmp_path / "config.toml"
        cfg = ConfigManager(path=path)
        cfg.set_resource_defaults({"max_inflight_queries": 6, "max_row_limit": 777, "blank": ""})
        reloaded = ConfigManager(path=path)
        defaults = reloaded.resource_defaults()
        assert defaults["max_inflight_queries"] == 6
        assert defaults["max_row_limit"] == 777
        assert "blank" not in defaults  # empty dropped

    def test_policy_for_merges_profile_and_overrides(self, tmp_path):
        from dbaide.db import policy as policy_mod
        policy_mod.clear_cache()
        path = tmp_path / "config.toml"
        cfg = ConfigManager(path=path)
        cfg.set_resource_defaults({"max_row_limit": 250})
        cfg.upsert_connection(ConnectionConfig(name="c1", type="sqlite", path="/tmp/x.db", load_profile="staging"))
        policy_mod.clear_cache()
        policy = cfg.policy_for(cfg.connections()["c1"])
        # staging preset, but max_row_limit overridden
        assert policy.build_profile_mode == "auto"
        assert policy.max_row_limit == 250

    def test_connection_save_clears_per_instance_policy_cache(self, tmp_path):
        from dbaide.db import policy as policy_mod
        policy_mod.clear_cache()
        path = tmp_path / "config.toml"
        cfg = ConfigManager(path=path)
        cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path="/tmp/a.db", load_profile="production"))
        first = cfg.policy_for(cfg.connections()["local"])
        assert first.build_max_workers == 4

        cfg.upsert_connection(ConnectionConfig(name="local", type="sqlite", path="/tmp/a.db", load_profile="dev"))
        second = cfg.policy_for(cfg.connections()["local"])

        assert second.build_max_workers == 4
