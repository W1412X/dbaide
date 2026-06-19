"""Unified agent step budget limits and helpers.

Agent *budget* steps (tool-loop iterations) are separate from trace *timeline*
units (visible cards in the trace drawer). Budget constants live here; timeline
counting lives in ``trace_model`` (``count_timeline_steps`` / ``step_count_from_events``).
"""

from __future__ import annotations

from typing import Any

# Tool-loop iteration budget (``AgentRuntime.max_steps`` / ``Session.agent_max_steps``).
DEFAULT_AGENT_MAX_STEPS = 128
MIN_AGENT_MAX_STEPS = 1
MAX_AGENT_MAX_STEPS = 256

# Subagent delegation cap (always <= parent budget).
MIN_SUBAGENT_MAX_STEPS = 4
MAX_SUBAGENT_MAX_STEPS = 32
DEFAULT_SUBAGENT_STEPS_WHEN_UNSPECIFIED = 24


def clamp_agent_max_steps(value: Any, *, default: int = DEFAULT_AGENT_MAX_STEPS) -> int:
    """Normalize a user/policy ``agent_max_steps`` value."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    return max(MIN_AGENT_MAX_STEPS, min(n, MAX_AGENT_MAX_STEPS))


def child_step_budget(requested: Any, parent_max: Any) -> int:
    """Budget for a ``run_subagent`` child loop, capped by the parent session."""
    parent = clamp_agent_max_steps(parent_max)
    try:
        n = int(requested)
    except (TypeError, ValueError):
        half = max(MIN_SUBAGENT_MAX_STEPS, parent // 2)
        n = min(DEFAULT_SUBAGENT_STEPS_WHEN_UNSPECIFIED, half)
    return max(MIN_SUBAGENT_MAX_STEPS, min(n, parent, MAX_SUBAGENT_MAX_STEPS))
