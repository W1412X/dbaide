from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from dbaide.models import ConnectionConfig


def connection_identity(connection: ConnectionConfig | None) -> dict[str, Any]:
    """Stable, non-secret identity for cache scoping.

    Passwords and password env names are intentionally excluded. The identity is used
    only to decide whether local schema assets / join hints belong to the same target.
    """
    if connection is None:
        return {}
    conn_type = str(connection.type or "").strip().lower()
    path = str(connection.path or "").strip()
    if conn_type == "sqlite" and path and path != ":memory:":
        try:
            path = str(Path(path).expanduser().resolve())
        except OSError:
            path = str(Path(path).expanduser().absolute())
    return {
        "type": conn_type,
        "host": str(connection.host or "").strip().lower(),
        "port": int(connection.port) if connection.port else None,
        "database": str(connection.database or "").strip(),
        "user": str(connection.user or "").strip(),
        "path": path,
    }


def connection_fingerprint(connection: ConnectionConfig | None) -> str:
    identity = connection_identity(connection)
    if not identity:
        return ""
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_matches(stored: str, expected: str) -> bool:
    return bool(stored and expected and stored == expected)
