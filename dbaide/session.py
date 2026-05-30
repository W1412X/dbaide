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

