"""End-to-end: the FINAL answer streams token-by-token via the decide call, gated by
the stream_answers flag. Intermediate (call_tool) decisions carry no answer field, so
nothing streams for them — only the final answer does."""

import json
import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.assets import AssetBuilder, AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.llm import LLMClient
from dbaide.models import ConnectionConfig
from dbaide.session import Session

ANSWER = "42 paid orders。你好\nsecond line"


class StreamMock(LLMClient):
    """Finishes on the first decision; streams the decide JSON in small slices."""

    def supports_streaming(self):
        return True

    def complete_json(self, messages, *, schema_hint=""):
        system = messages[0].content if messages else ""
        if "tool loop" in system.lower():
            return {"action": "finish", "answer": ANSWER}
        return {}

    def complete_json_stream(self, messages, *, schema_hint="", on_text_chunk=None):
        payload = self.complete_json(messages, schema_hint=schema_hint)
        text = json.dumps(payload, ensure_ascii=False)
        if on_text_chunk:
            for i in range(0, len(text), 5):       # 5-char slices → forces split escapes
                on_text_chunk(text[i:i + 5])
        return payload

    def complete_text(self, messages):
        return "OK"


def _orch(tmp_path, progress):
    db = tmp_path / "shop.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE orders(id INTEGER PRIMARY KEY, amount REAL, status TEXT);"
        "INSERT INTO orders VALUES (1, 9.9, 'paid');"
    )
    c.commit(); c.close()
    conn = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    store = AssetStore(tmp_path / "assets")
    jc = JoinCatalogStore(base_dir=tmp_path / "joins")
    AssetBuilder(connection=conn, adapter=build_adapter(conn), store=store,
                 join_catalog=jc).build(profile_mode="none", sample=False)
    return AskOrchestrator(build_adapter(conn), Session(connection=conn), StreamMock(),
                           asset_store=store, join_catalog=jc, progress=progress)


def test_openai_client_complete_json_stream_emits_and_parses(monkeypatch):
    """The REAL client must stream raw deltas to on_text_chunk AND parse the JSON.
    (Regression: the client previously inherited a non-streaming complete_json_stream,
    so on_text_chunk never fired against a live model.)"""
    import urllib.request

    from dbaide.llm import LLMMessage, OpenAICompatibleClient
    from dbaide.models import ModelConfig

    client = OpenAICompatibleClient(ModelConfig(
        provider="openai_compatible", base_url="http://x/v1", api_key="k", model="m"))
    body = '{"action":"finish","answer":"hi 你好"}'

    def fake_lines():
        for i in range(0, len(body), 6):
            yield (f'data: {json.dumps({"choices":[{"delta":{"content":body[i:i+6]}}]})}\n').encode()
        yield b"data: [DONE]\n"

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return fake_lines()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0, **kw: FakeResp())
    seen: list[str] = []
    payload = client.complete_json_stream(
        [LLMMessage("user", "q")], schema_hint="Return JSON", on_text_chunk=seen.append)
    assert "".join(seen) == body          # raw deltas streamed through
    assert payload == {"action": "finish", "answer": "hi 你好"}  # and parsed


def test_stream_propagates_callback_exception(monkeypatch):
    """A cancellation raised by on_chunk during streaming must propagate — NOT be
    swallowed into a wasteful non-stream re-request."""
    import urllib.request

    from dbaide.llm import LLMMessage, OpenAICompatibleClient
    from dbaide.models import ModelConfig

    client = OpenAICompatibleClient(ModelConfig(
        provider="openai_compatible", base_url="http://x/v1", api_key="k", model="m"))

    def fake_lines():
        for piece in ["a", "b", "c"]:
            yield (f'data: {json.dumps({"choices":[{"delta":{"content":piece}}]})}\n').encode()

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return fake_lines()

    calls = {"text": 0}
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0, **kw: FakeResp())
    monkeypatch.setattr(client, "complete_text",
                        lambda *a, **k: calls.__setitem__("text", calls["text"] + 1) or "full")

    class Cancel(Exception):
        pass

    def boom(_delta):
        raise Cancel()

    import pytest
    with pytest.raises(Cancel):
        client.complete_text_stream([LLMMessage("user", "q")], on_chunk=boom)
    assert calls["text"] == 0          # no fallback re-request happened


def _chunks_collector():
    chunks: list[str] = []

    def progress(msg):
        if isinstance(msg, dict) and msg.get("kind") == "answer_chunk":
            chunks.append(str(msg.get("text") or ""))
    return chunks, progress


def test_final_answer_streams_when_enabled(tmp_path):
    chunks, progress = _chunks_collector()
    orch = _orch(tmp_path, progress)
    orch.stream_answers = True
    resp = orch.run("how many paid orders", execute=True)
    # Streamed slices reassemble to exactly the final answer (escapes/unicode intact).
    assert chunks, "expected streamed answer chunks"
    assert "".join(chunks) == ANSWER
    assert ANSWER in resp.answer            # authoritative answer matches the stream


def test_no_streaming_when_disabled(tmp_path):
    chunks, progress = _chunks_collector()
    orch = _orch(tmp_path, progress)
    orch.stream_answers = False
    resp = orch.run("how many paid orders", execute=True)
    assert chunks == []                     # non-stream path → no answer_chunk events
    assert ANSWER in resp.answer            # answer still correct


def test_stream_view_height_tracks_wrapped_content():
    """Regression: the streaming QPlainTextEdit height must reflect the wrapped pixel height,
    not QPlainTextEdit.document().size().height() (which is a line COUNT) — otherwise the view
    clips to ~one line during streaming."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from dbaide.desktop.components.conversation import _MarkdownBlock

    app = QApplication.instance() or QApplication([])
    block = _MarkdownBlock("")
    block.resize(420, 800)
    block.show()
    app.processEvents()
    long = "This is a sentence that will wrap across multiple lines when shown in a narrow block. "
    block.set_streaming_text((long * 3 + "\n") * 4)   # ~12+ wrapped lines
    app.processEvents()
    block._sync_stream_height()
    view = block._stream_view
    assert view is not None
    line_spacing = view.fontMetrics().lineSpacing()
    # Far more than one or two lines — the old line-count-as-pixels bug capped this near ~24px.
    assert view.height() > 6 * line_spacing
    block.deleteLater()


def test_stream_height_grows_during_incremental_streaming():
    """Real scenario: as chunks arrive the view height must keep up with content, not stay
    clipped (the timer-debounce starved during continuous streaming, and QPlainTextEdit
    reported a stale line count — both fixed by QTextEdit + per-chunk pixel measure)."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from dbaide.desktop.components.conversation import _MarkdownBlock

    app = QApplication.instance() or QApplication([])
    block = _MarkdownBlock("")
    block.resize(420, 800)
    block.show()
    app.processEvents()

    para = "This is a chunk of streamed answer text that wraps over a couple of lines. "
    heights = []
    acc = ""
    for _ in range(8):
        acc += para * 2 + "\n\n"
        block.set_streaming_text(acc)        # contentsChanged → _sync_stream_height per chunk
        app.processEvents()
        heights.append(block._stream_view.height())

    ls = block._stream_view.fontMetrics().lineSpacing()
    assert heights == sorted(heights)        # monotonic growth, never clipped back to one line
    assert heights[-1] > heights[0] + 8 * ls  # grew by many lines over the stream
    block.deleteLater()
