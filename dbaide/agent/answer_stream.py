"""Best-effort live extraction of a JSON string field from a streaming completion.

The agent's final answer arrives inside the decide call's JSON
(``{"action":"finish","answer":"..."}``). We stream that call's raw text and surface
the ``answer`` field's characters as they arrive — purely to drive the live UI
("first token faster"). Correctness never depends on this: the caller still parses the
full JSON afterward for the authoritative decision/answer, so a slightly-off live
extraction can't corrupt the result. Intermediate ``call_tool`` decisions carry no
``answer`` field, so nothing streams for them — only the final answer does.
"""
from __future__ import annotations

import json
import re
from typing import Callable


class JsonFieldStreamer:
    """Feed it raw text deltas of a JSON object; it emits the incremental decoded value
    of one string field as it arrives."""

    def __init__(self, on_text: Callable[[str], None], *, field: str = "answer") -> None:
        self._on_text = on_text
        self._buf = ""
        self._emitted = 0
        self._key_re = re.compile(r'"' + re.escape(field) + r'"\s*:\s*"')

    def feed(self, delta: str) -> None:
        if not delta:
            return
        self._buf += delta
        value = self._partial_value()
        if value is None or len(value) <= self._emitted:
            return
        self._on_text(value[self._emitted:])
        self._emitted = len(value)

    def _partial_value(self) -> str | None:
        m = self._key_re.search(self._buf)
        if not m:
            return None
        buf = self._buf
        n = len(buf)
        i = m.end()
        raw: list[str] = []
        while i < n:
            ch = buf[i]
            if ch == "\\":
                if i + 1 >= n:            # incomplete escape at the tail → wait for more
                    break
                if buf[i + 1] == "u":
                    if i + 6 > n:         # incomplete \uXXXX → wait for the rest
                        break
                    raw.append(buf[i:i + 6])
                    i += 6
                    continue
                raw.append(buf[i:i + 2])
                i += 2
                continue
            if ch == '"':                 # closing quote of the value
                break
            raw.append(ch)
            i += 1
        try:
            return json.loads('"' + "".join(raw) + '"')
        except ValueError:
            return None
