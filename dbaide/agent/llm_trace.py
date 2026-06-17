"""Debug-level LLM call recording for the trace.

When ``DBAIDE_TRACE_LLM`` is set, the orchestrator wraps its LLM client in a
``RecordingLLMClient``. Every model call (decide, discover filter, schema link,
clarify, sql writer, …) is captured WITH ITS FULL system+user prompt and raw
response, tagged by the active stage. The loop attaches the calls made during
each tool step to that step's trace event, so a copied/exported trace shows the
exact context fed to the model at every stage — i.e. a real debug view.

Off by default (no capture, zero overhead) so normal runs and persisted sessions
stay lean.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import time
from typing import Any, Callable

from dbaide.llm import LLMClient, LLMMessage

# The stage the current LLM call belongs to (e.g. a tool name, or "decide").
_active_stage: contextvars.ContextVar[str] = contextvars.ContextVar("dbaide_llm_stage", default="")

# Process-level override (set by the desktop launcher from config), so the GUI can
# enable debug tracing without an env var. None = not set → fall back to the env var.
_forced: bool | None = None


def set_tracing(on: bool | None) -> None:
    """Force tracing on/off for this process (None reverts to the env-var gate)."""
    global _forced
    _forced = on


def tracing_enabled() -> bool:
    if _forced is not None:
        return _forced
    return bool(os.environ.get("DBAIDE_TRACE_LLM"))


@contextlib.contextmanager
def llm_stage(label: str):
    """Tag every LLM call made inside this block with ``label``."""
    token = _active_stage.set(label or "")
    try:
        yield
    finally:
        _active_stage.reset(token)


def _dump(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(value)


class RecordingLLMClient(LLMClient):
    """Transparent proxy that records full prompt/response for each call."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    # -- recording helpers ----------------------------------------------------

    def _messages(self, messages: list[LLMMessage]) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def _record(self, method: str, messages: list[LLMMessage], response: Any, ms: float) -> None:
        self.calls.append({
            "stage": _active_stage.get(),
            "method": method,
            "messages": self._messages(messages),
            "response": _dump(response),
            "ms": round(ms, 1),
        })

    def snapshot_len(self) -> int:
        return len(self.calls)

    def since(self, start: int) -> list[dict[str, Any]]:
        return [dict(c) for c in self.calls[start:]]

    # -- proxied LLM API ------------------------------------------------------

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict[str, Any]:
        t = time.perf_counter()
        try:
            out = self.inner.complete_json(messages, schema_hint=schema_hint)
            return out
        finally:
            self._record("complete_json", messages, locals().get("out"), (time.perf_counter() - t) * 1000)

    def complete_text(self, messages: list[LLMMessage], *, json_mode: bool = False) -> str:
        t = time.perf_counter()
        try:
            out = self.inner.complete_text(messages, json_mode=json_mode)
            return out
        finally:
            self._record("complete_text", messages, locals().get("out"), (time.perf_counter() - t) * 1000)

    def supports_streaming(self) -> bool:
        return self.inner.supports_streaming()

    def complete_json_stream(self, messages: list[LLMMessage], *, schema_hint: str = "",
                             on_text_chunk: "Callable[[str], None] | None" = None) -> dict[str, Any]:
        t = time.perf_counter()
        try:
            out = self.inner.complete_json_stream(messages, schema_hint=schema_hint, on_text_chunk=on_text_chunk)
            return out
        finally:
            self._record("complete_json_stream", messages, locals().get("out"), (time.perf_counter() - t) * 1000)

    def complete_text_stream(self, messages: list[LLMMessage],
                             on_chunk: "Callable[[str], None]",
                             *, json_mode: bool = False) -> str:
        t = time.perf_counter()
        try:
            out = self.inner.complete_text_stream(messages, on_chunk, json_mode=json_mode)
            return out
        finally:
            self._record("complete_text_stream", messages, locals().get("out"), (time.perf_counter() - t) * 1000)
