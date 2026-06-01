from __future__ import annotations

from dataclasses import dataclass, field

from .context.disclosure import DisclosureContext
from .models import ConnectionConfig


@dataclass(slots=True)
class Session:
    connection: ConnectionConfig
    disclosure: DisclosureContext = field(default_factory=DisclosureContext)
    default_limit: int = 100
    timeout_seconds: int = 10

    @classmethod
    def from_policy(cls, connection: ConnectionConfig, policy, **kwargs) -> "Session":
        """Build a session whose limits default to the connection's ResourcePolicy."""
        kwargs.setdefault("default_limit", policy.default_row_limit)
        kwargs.setdefault("timeout_seconds", policy.statement_timeout_seconds)
        return cls(connection=connection, **kwargs)

