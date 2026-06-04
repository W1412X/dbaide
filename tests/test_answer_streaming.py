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
