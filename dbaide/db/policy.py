"""Resource policy: per-connection load profiles + user-configurable overrides.

A :class:`ResourcePolicy` carries every numeric knob that governs how much load
DBAide is allowed to put on a database. Values come from a named ``load_profile``
preset (``production`` / ``staging`` / ``dev``) and are then overridden, key by
key, with whatever the user put in ``[resource_defaults]`` of ``config.toml``.

The defaults are deliberately conservative: a fresh connection with no explicit
profile is treated as ``production`` so that an AI assistant never hammers a live
database by accident.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, fields, replace
from typing import Any

# Profiling tiers, ordered from cheapest to most expensive.
PROFILE_MODES = ("none", "light", "auto", "all")
LOAD_PROFILE_NAMES = ("production", "staging", "dev")
DEFAULT_LOAD_PROFILE = "production"


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """All tunable resource limits. Every field is a concrete, user-configurable number.

    One ``QueryBudget`` semaphore per instance caps concurrent in-flight queries; because
    a query holds its connection for its whole duration, ``max_inflight_queries`` bounds
    both concurrency *and* simultaneous connections — there is intentionally no separate
    connection knob.
    """

    # Concurrency (enforced by QueryBudget; one slot == one query == one connection).
    max_inflight_queries: int = 16
    statement_timeout_seconds: int = 60

    # Asset build.
    build_max_workers: int = 4
    build_profile_mode: str = "light"

    # Agent execution.
    default_row_limit: int = 100
    max_row_limit: int = 1000

    # Agent reasoning budget (how hard the agent is allowed to work per question).
    agent_max_steps: int = 64            # tool-loop iterations before the agent must answer

    # Cost gates.
    big_table_rows: int = 1_000_000      # estimated rows above which profiling drops to metadata-only
    explain_max_rows: int = 5_000_000    # EXPLAIN estimate above which execution is blocked

    # Join sampling (rows sampled from the left table when probing a join).
    join_sample_size: int = 150

    def merged_with(self, overrides: dict[str, Any]) -> "ResourcePolicy":
        """Return a copy with any recognised numeric/string overrides applied."""
        if not overrides:
            return self
        valid = {f.name for f in fields(self)}
        clean: dict[str, Any] = {}
        for key, value in overrides.items():
            if key not in valid or value is None:
                continue
            current = getattr(self, key)
            try:
                clean[key] = type(current)(value)
            except (TypeError, ValueError):
                continue
        return replace(self, **clean) if clean else self

    @classmethod
    def for_load_profile(cls, name: str) -> "ResourcePolicy":
        return LOAD_PROFILES.get(_normalize_profile(name), LOAD_PROFILES[DEFAULT_LOAD_PROFILE])


# Three presets. ``production`` is the conservative default.
LOAD_PROFILES: dict[str, ResourcePolicy] = {
    "production": ResourcePolicy(
        max_inflight_queries=16,
        statement_timeout_seconds=60,
        build_max_workers=4,
        build_profile_mode="light",
        default_row_limit=100,
        max_row_limit=1000,
        agent_max_steps=64,
        big_table_rows=1_000_000,
        explain_max_rows=5_000_000,
        join_sample_size=150,
    ),
    "staging": ResourcePolicy(
        max_inflight_queries=16,
        statement_timeout_seconds=60,
        build_max_workers=2,
        build_profile_mode="auto",
        default_row_limit=100,
        max_row_limit=5000,
        agent_max_steps=64,
        big_table_rows=5_000_000,
        explain_max_rows=20_000_000,
        join_sample_size=150,
    ),
    "dev": ResourcePolicy(
        max_inflight_queries=16,
        statement_timeout_seconds=60,
        build_max_workers=4,
        build_profile_mode="auto",
        default_row_limit=200,
        max_row_limit=50000,
        agent_max_steps=64,
        big_table_rows=50_000_000,
        explain_max_rows=200_000_000,
        join_sample_size=200,
    ),
}


def _normalize_profile(name: str | None) -> str:
    value = str(name or "").strip().lower()
    return value if value in LOAD_PROFILES else DEFAULT_LOAD_PROFILE


# ── Per-instance policy resolution (cached) ──────────────────────────────────

_lock = threading.Lock()
_cache: dict[str, ResourcePolicy] = {}


def resolve_policy(
    *,
    load_profile: str | None,
    overrides: dict[str, Any] | None = None,
    instance: str = "",
) -> ResourcePolicy:
    """Resolve the effective policy: preset(load_profile) overridden by ``overrides``.

    Results are cached per instance name so repeated ``build_adapter`` calls are cheap.
    Pass ``instance=""`` to bypass the cache (used by tests).
    """
    if instance:
        with _lock:
            cached = _cache.get(instance)
            if cached is not None:
                return cached
    policy = ResourcePolicy.for_load_profile(load_profile or DEFAULT_LOAD_PROFILE)
    policy = policy.merged_with(overrides or {})
    if instance:
        with _lock:
            _cache.setdefault(instance, policy)
            return _cache[instance]
    return policy


def clear_cache(instance: str | None = None) -> None:
    """Drop cached policies (call after the user edits resource_defaults)."""
    with _lock:
        if instance is None:
            _cache.clear()
        else:
            _cache.pop(instance, None)
