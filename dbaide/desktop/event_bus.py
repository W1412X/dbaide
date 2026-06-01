"""A tiny synchronous publish/subscribe bus for the desktop UI.

The desktop controller used to refresh components imperatively: every action
handler had to remember which widgets to reload (``refresh_all`` here,
``refresh_joins`` there, ``_load_history`` elsewhere). That couples producers of a
change to every consumer and makes it easy to leave a component showing stale data
after an action finishes.

``EventBus`` decouples them: an action emits a *semantic* event ("assets changed")
and any number of subscribers re-fetch what they need. Adding a new component that
must react to a change becomes a one-line ``subscribe`` instead of editing every
action handler.

It is intentionally plain Python (no Qt) so the wiring is unit-testable. Emission
is synchronous, so callers are responsible for emitting on the GUI thread when
subscribers touch widgets — the desktop controller emits from its result handlers,
which already run on the GUI thread.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("dbaide.eventbus")

# ── Topics ───────────────────────────────────────────────────────────────────
# The set of data-change events components can subscribe to. Payload is a dict
# (e.g. {"instance": "prod"}) or None.
CONNECTIONS_CHANGED = "connections_changed"   # added/edited/removed a connection
MODELS_CHANGED = "models_changed"             # added/edited/removed/selected a model
ASSETS_CHANGED = "assets_changed"             # offline assets (re)built for an instance
JOINS_CHANGED = "joins_changed"               # join catalog mutated for an instance
QUERY_COMPLETED = "query_completed"           # a query/ask ran (query log grew)
CONNECTION_SELECTED = "connection_selected"   # the active connection changed

ALL_TOPICS = frozenset({
    CONNECTIONS_CHANGED,
    MODELS_CHANGED,
    ASSETS_CHANGED,
    JOINS_CHANGED,
    QUERY_COMPLETED,
    CONNECTION_SELECTED,
})


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[Any], None]]] = {}

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> Callable[[], None]:
        """Register ``callback`` for ``topic``. Returns an unsubscribe function."""
        self._subs.setdefault(topic, []).append(callback)

        def _unsubscribe() -> None:
            handlers = self._subs.get(topic)
            if handlers and callback in handlers:
                handlers.remove(callback)

        return _unsubscribe

    def emit(self, topic: str, payload: Any = None) -> None:
        """Notify every subscriber of ``topic``. A failing subscriber never blocks
        the others (its exception is logged and swallowed)."""
        for callback in list(self._subs.get(topic, ())):
            try:
                callback(payload)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break the rest
                logger.exception("event-bus subscriber for %s failed", topic)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subs.get(topic, ()))

    def clear(self) -> None:
        self._subs.clear()
