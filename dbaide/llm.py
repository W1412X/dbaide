from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .models import ModelConfig

logger = logging.getLogger("dbaide.llm")


@dataclass(slots=True)
class LLMMessage:
    role: str
    content: str


class LLMClient:
    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict[str, Any]:
        raise NotImplementedError

    def complete_text(self, messages: list[LLMMessage]) -> str:
        raise NotImplementedError

    def supports_streaming(self) -> bool:
        return False

    def complete_json_stream(self, messages: list[LLMMessage], *, schema_hint: str = "",
                             on_text_chunk: "Callable[[str], None] | None" = None) -> dict[str, Any]:
        """Like complete_json, but streams raw text deltas to ``on_text_chunk`` (for a
        caller that wants to surface a field live). Base fallback: no streaming."""
        return self.complete_json(messages, schema_hint=schema_hint)

    def complete_text_stream(self, messages: list[LLMMessage],
                             on_chunk: "Callable[[str], None]") -> str:
        """Stream the completion, calling ``on_chunk`` with each text delta; returns
        the full text. Base fallback: do a normal completion and emit it as one chunk
        (so callers can always use this API)."""
        text = self.complete_text(messages)
        if text:
            on_chunk(text)
        return text


class NullLLMClient(LLMClient):
    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict[str, Any]:
        raise RuntimeError("No LLM model configured. Use heuristic fallback or configure a model.")

    def complete_text(self, messages: list[LLMMessage]) -> str:
        raise RuntimeError("No LLM model configured. Use heuristic fallback or configure a model.")


class OpenAICompatibleClient(LLMClient):
    MAX_RETRIES = 3
    RETRY_BACKOFF = (1, 2, 4)

    def __init__(self, cfg: ModelConfig) -> None:
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        self.api_key = cfg.api_key or (os.environ.get(cfg.api_key_env) if cfg.api_key_env else "")
        if not self.base_url or not self.api_key or not cfg.model:
            missing = []
            if not self.base_url:
                missing.append("Base URL")
            if not cfg.model:
                missing.append("Model ID")
            if not self.api_key:
                missing.append("API Key (or set api_key_env)")
            raise ValueError(
                "OpenAI-compatible model is incomplete. Missing: "
                + ", ".join(missing)
                + ". Fill all fields in Settings → Models."
            )

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict[str, Any]:
        text = self.complete_text(messages + ([LLMMessage("system", schema_hint)] if schema_hint else []))
        return self._parse_json_object(text)

    def complete_text(self, messages: list[LLMMessage]) -> str:
        payload = {
            "model": self.cfg.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        timeout = max(1, int(self.cfg.timeout_seconds))
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                start = time.perf_counter()
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                elapsed = (time.perf_counter() - start) * 1000
                logger.debug("llm_response model=%s elapsed_ms=%.0f attempt=%d", self.cfg.model, elapsed, attempt + 1)
                if "error" in data:
                    raise RuntimeError(f"LLM API error: {data['error']}")
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError("LLM returned empty choices")
                content = choices[0].get("message", {}).get("content")
                if content is None:
                    raise RuntimeError("LLM returned null content")
                return str(content)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_exc = RuntimeError(f"LLM HTTP {exc.code}: {body[:200]}")
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_BACKOFF[min(attempt, len(self.RETRY_BACKOFF) - 1)]
                    logger.warning("llm_retry attempt=%d delay=%ds status=%d", attempt + 1, delay, exc.code)
                    time.sleep(delay)
                    continue
                raise last_exc from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = RuntimeError(f"LLM connection failed: {exc}")
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_BACKOFF[min(attempt, len(self.RETRY_BACKOFF) - 1)]
                    logger.warning("llm_retry attempt=%d delay=%ds error=%s", attempt + 1, delay, exc)
                    time.sleep(delay)
                    continue
                raise last_exc from exc
        raise last_exc or RuntimeError("LLM call failed after retries")

    def supports_streaming(self) -> bool:
        return True

    def complete_json_stream(self, messages: list[LLMMessage], *, schema_hint: str = "",
                             on_text_chunk: "Callable[[str], None] | None" = None) -> dict[str, Any]:
        """Stream the completion (forwarding raw text deltas to ``on_text_chunk``) and
        parse the accumulated text as a JSON object — same contract as complete_json,
        but lets a caller surface a field live."""
        msgs = messages + ([LLMMessage("system", schema_hint)] if schema_hint else [])
        text = self.complete_text_stream(msgs, on_chunk=on_text_chunk or (lambda _d: None))
        return self._parse_json_object(text)

    def complete_text_stream(self, messages: list[LLMMessage],
                             on_chunk: "Callable[[str], None]") -> str:
        """Stream the chat completion via SSE (``stream: true``), emitting each content
        delta through ``on_chunk`` and returning the accumulated text. Any streaming
        failure falls back to a normal (non-streamed) completion so the answer is never
        lost."""
        payload = {
            "model": self.cfg.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0,
            "stream": True,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        timeout = max(1, int(self.cfg.timeout_seconds))
        parts: list[str] = []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for raw in resp:                         # SSE: one "data: {...}" per line
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        parts.append(str(delta))
                        on_chunk(str(delta))
            if parts:
                return "".join(parts)
        except Exception as exc:  # noqa: BLE001 — fall back to a normal completion
            logger.warning("llm_stream_failed, falling back to non-stream: %s", exc)
        # Fallback: a normal completion, emitted as a single chunk.
        text = self.complete_text(messages)
        if text and not parts:
            on_chunk(text)
        return text or "".join(parts)

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
        result = json.loads(stripped)
        if not isinstance(result, dict):
            raise ValueError(f"Expected JSON object, got {type(result).__name__}")
        return result


def build_llm_client(cfg: ModelConfig) -> LLMClient:
    if cfg.provider in {"none", ""}:
        return NullLLMClient()
    if cfg.provider in {"openai_compatible", "openai-compatible", "openai"}:
        return OpenAICompatibleClient(cfg)
    raise ValueError(f"Unknown LLM provider: {cfg.provider!r}. Supported: openai_compatible, openai-compatible, openai, none")
