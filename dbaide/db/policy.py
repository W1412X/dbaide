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

from dbaide.step_budget import DEFAULT_AGENT_MAX_STEPS

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
    agent_max_steps: int = DEFAULT_AGENT_MAX_STEPS            # tool-loop iterations before the agent must answer
    prior_turns_window: int = 3          # how many prior session turns to show in the agent prompt
    max_batch_tools: int = 6             # max parallel tool calls the agent may issue per decision
    latest_result_limit: int = 0         # max chars of one tool result in the prompt; <=0 means unlimited

    # Cost gates.
    big_table_rows: int = 1_000_000      # estimated rows above which profiling drops to metadata-only
    explain_max_rows: int = 5_000_000    # EXPLAIN estimate above which execution is blocked
    optimize_advise_rows: int = 1_000_000  # EXPLAIN estimate above which the SQL advisor attaches
                                           # optimization suggestions to the agent (0 = off; advisory)
    optimize_advise_mode: str = "gate"     # gate: advise BEFORE executing a heavy query, let the
                                           # agent rewrite (once per query, no loop); suggest: run
                                           # then attach advice; off: no advisor in the agent flow

    # Join sampling (rows sampled from the left table when probing a join).
    join_sample_size: int = 150

    # Context compression trigger: compress when token estimate exceeds this
    # percentage of the model's context_length. Range 50–95; default 80.
    compress_threshold: int = 80

    # Session continuity: how many recent completed turns to keep uncompressed.
    session_uncompressed_turns: int = 2

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
        agent_max_steps=DEFAULT_AGENT_MAX_STEPS,
        prior_turns_window=3,
        max_batch_tools=6,

        latest_result_limit=0,
        big_table_rows=1_000_000,
        explain_max_rows=5_000_000,
        join_sample_size=150,
        session_uncompressed_turns=2,
    ),
    "staging": ResourcePolicy(
        max_inflight_queries=16,
        statement_timeout_seconds=60,
        build_max_workers=2,
        build_profile_mode="auto",
        default_row_limit=100,
        max_row_limit=5000,
        agent_max_steps=DEFAULT_AGENT_MAX_STEPS,
        prior_turns_window=3,
        max_batch_tools=6,

        latest_result_limit=0,
        big_table_rows=5_000_000,
        explain_max_rows=20_000_000,
        join_sample_size=150,
        session_uncompressed_turns=2,
    ),
    "dev": ResourcePolicy(
        max_inflight_queries=16,
        statement_timeout_seconds=60,
        build_max_workers=4,
        build_profile_mode="auto",
        default_row_limit=200,
        max_row_limit=50000,
        agent_max_steps=DEFAULT_AGENT_MAX_STEPS,
        prior_turns_window=5,
        max_batch_tools=6,

        latest_result_limit=0,
        big_table_rows=50_000_000,
        explain_max_rows=200_000_000,
        join_sample_size=200,
        session_uncompressed_turns=3,
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
