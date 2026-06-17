from __future__ import annotations

from dataclasses import dataclass, field

from .context.disclosure import DisclosureContext
from .models import ConnectionConfig


@dataclass(slots=True)
class Session:
    connection: ConnectionConfig
    disclosure: DisclosureContext = field(default_factory=DisclosureContext)
    default_limit: int = 100
    timeout_seconds: int = 60
    # Agent reasoning budget — defaults match ResourcePolicy so a directly-built
    # Session (e.g. in tests) behaves like the conservative "production" preset.
    agent_max_steps: int = 64
    prior_turns_window: int = 3
    max_batch_tools: int = 6
    latest_result_limit: int = 4000
    compress_threshold: int = 80

    @classmethod
    def from_policy(cls, connection: ConnectionConfig, policy, **kwargs) -> "Session":
        """Build a session whose limits default to the connection's ResourcePolicy."""
        kwargs.setdefault("default_limit", policy.default_row_limit)
        kwargs.setdefault("timeout_seconds", policy.statement_timeout_seconds)
        kwargs.setdefault("agent_max_steps", policy.agent_max_steps)
        kwargs.setdefault("prior_turns_window", policy.prior_turns_window)
        kwargs.setdefault("max_batch_tools", policy.max_batch_tools)
        kwargs.setdefault("latest_result_limit", policy.latest_result_limit)
        kwargs.setdefault("compress_threshold", policy.compress_threshold)
        return cls(connection=connection, **kwargs)
