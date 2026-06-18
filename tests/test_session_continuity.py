"""Tests for session-level continuous agent (session_messages feature)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop
from dbaide.agent.loop_prompts import estimate_tokens
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.core.result import WorkflowRequest, WorkflowResult
from dbaide.history.session_store import ChatSessionStore
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import AssistantResponse, ConnectionConfig
from dbaide.session import Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL);"
        "INSERT INTO orders VALUES (1, 42.0);"
    )
    conn.commit()
    conn.close()


def _orch(tmp_path, llm=None):
    db = tmp_path / "test.db"
    _make_db(db)
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    adapter = build_adapter(cfg)
    session = Session(connection=cfg)
    return AskOrchestrator(adapter, session, llm or _FinishNowLLM())


class _FinishNowLLM(LLMClient):
    """Always returns a finish action immediately."""

    def __init__(self, answer: str = "Done."):
        self._answer = answer

    def complete_json(self, messages: list[LLMMessage], **kw) -> dict[str, Any]:
        system = messages[0].content if messages else ""
        if "tool loop" in system.lower():
            return {"action": "finish", "answer": self._answer}
        if "relevant_indices" in system:
            return {"relevant_indices": [], "reason": "mock"}
        return {}

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "summary"


# ---------------------------------------------------------------------------
# _count_completed_turns / _find_turn_ranges (static helpers)
# ---------------------------------------------------------------------------

class TestTurnHelpers:
    def test_count_completed_turns_empty(self):
        assert AskAgentLoop._count_completed_turns([]) == 0

    def test_count_completed_turns_one(self):
        msgs = [
            LLMMessage("system", "sys"),
            LLMMessage("user", "[turn:1:start]\nHello"),
            LLMMessage("assistant", "Hi"),
            LLMMessage("user", "[turn:1:end] Answer delivered."),
        ]
        assert AskAgentLoop._count_completed_turns(msgs) == 1

    def test_count_completed_turns_ignores_incomplete(self):
        msgs = [
            LLMMessage("system", "sys"),
            LLMMessage("user", "[turn:1:start]\nHello"),
            LLMMessage("assistant", "Hi"),
            LLMMessage("user", "[turn:1:end] Answer delivered."),
            LLMMessage("user", "[turn:2:start]\nFollowup"),
            # no turn:2:end — still in progress
        ]
        assert AskAgentLoop._count_completed_turns(msgs) == 1

    def test_find_turn_ranges(self):
        msgs = [
            LLMMessage("system", "sys"),
            LLMMessage("user", "[turn:1:start]\nQ1"),
            LLMMessage("assistant", "A1"),
            LLMMessage("user", "[turn:1:end] Answer delivered."),
            LLMMessage("user", "[turn:2:start]\nQ2"),
            LLMMessage("assistant", "A2"),
            LLMMessage("user", "[turn:2:end] Answer delivered."),
        ]
        ranges = AskAgentLoop._find_turn_ranges(msgs)
        assert ranges == [(1, 3), (4, 6)]

    def test_find_turn_ranges_ignores_incomplete(self):
        msgs = [
            LLMMessage("system", "sys"),
            LLMMessage("user", "[turn:1:start]\nQ1"),
            LLMMessage("assistant", "A1"),
            # no end marker
        ]
        assert AskAgentLoop._find_turn_ranges(msgs) == []


# ---------------------------------------------------------------------------
# Session continuity: single-intent
# ---------------------------------------------------------------------------

class TestSessionContinuity:
    def test_first_turn_creates_session_messages(self, tmp_path):
        """First turn with empty session_messages produces turn markers."""
        orch = _orch(tmp_path)
        session_msgs = [
            LLMMessage("system", "placeholder"),
            LLMMessage("user", "placeholder"),
        ]
        orch.session_messages = session_msgs

        loop = AskAgentLoop(orch)
        loop.run("统计订单", session_messages=session_msgs)

        result_msgs = orch.session_messages
        assert result_msgs is not None
        # Should contain a turn start marker
        starts = [m for m in result_msgs if m.role == "user"
                  and m.content.startswith("[turn:") and ":start]" in m.content[:30]]
        assert len(starts) >= 1
        # Should contain a turn end marker
        ends = [m for m in result_msgs if m.role == "user"
                and m.content.startswith("[turn:") and ":end]" in m.content[:30]]
        assert len(ends) >= 1

    def test_none_session_messages_uses_isolation(self, tmp_path):
        """When session_messages is None, per-turn isolation is used."""
        orch = _orch(tmp_path)
        loop = AskAgentLoop(orch)
        loop.run("统计订单", session_messages=None)
        # orch.session_messages should remain None — not set by the loop
        assert orch.session_messages is None

    def test_second_turn_preserves_first_turn(self, tmp_path):
        """Messages from the first turn are preserved in the second turn's stream."""
        orch = _orch(tmp_path)
        initial_msgs = [
            LLMMessage("system", "old system prompt"),
            LLMMessage("user", "[turn:1:start]\nFirst question"),
            LLMMessage("assistant", "First answer"),
            LLMMessage("user", "[turn:1:end] Answer delivered."),
        ]
        orch.session_messages = list(initial_msgs)

        loop = AskAgentLoop(orch)
        loop.run("统计订单", session_messages=list(initial_msgs))

        result_msgs = orch.session_messages
        assert result_msgs is not None
        # System prompt should be updated (not the old one)
        assert result_msgs[0].role == "system"
        assert result_msgs[0].content != "old system prompt"
        # First turn markers should still be present
        turn1_start = any(
            m.role == "user" and "[turn:1:start]" in m.content
            for m in result_msgs
        )
        turn1_end = any(
            m.role == "user" and "[turn:1:end]" in m.content
            for m in result_msgs
        )
        assert turn1_start
        assert turn1_end
        # Second turn should be added
        turn2_start = any(
            m.role == "user" and "[turn:2:start]" in m.content
            for m in result_msgs
        )
        turn2_end = any(
            m.role == "user" and "[turn:2:end]" in m.content
            for m in result_msgs
        )
        assert turn2_start
        assert turn2_end


# ---------------------------------------------------------------------------
# Session store: load/save messages
# ---------------------------------------------------------------------------

class TestSessionStoreMessages:
    def test_save_and_load_messages(self, tmp_path):
        store = ChatSessionStore(Path(tmp_path))
        session = store.create("conn1")
        sid = session["session_id"]

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        store.save_messages("conn1", sid, messages)

        loaded = store.load_messages("conn1", sid)
        assert loaded == messages

    def test_load_messages_returns_none_for_missing(self, tmp_path):
        store = ChatSessionStore(Path(tmp_path))
        result = store.load_messages("conn1", "nonexistent")
        assert result is None

    def test_load_messages_returns_none_when_no_messages_field(self, tmp_path):
        store = ChatSessionStore(Path(tmp_path))
        session = store.create("conn1")
        sid = session["session_id"]
        # The fresh session has no "messages" field
        loaded = store.load_messages("conn1", sid)
        assert loaded is None


# ---------------------------------------------------------------------------
# WorkflowRequest / WorkflowResult session_messages
# ---------------------------------------------------------------------------

class TestWorkflowSessionMessages:
    def test_request_carries_session_messages(self):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        req = WorkflowRequest(
            question="test",
            connection_name="c",
            session_messages=msgs,
        )
        assert req.session_messages == msgs

    def test_request_default_none(self):
        req = WorkflowRequest(question="test", connection_name="c")
        assert req.session_messages is None

    def test_result_has_session_messages_slot(self):
        r = WorkflowResult(question="test")
        assert r.session_messages is None
        r.session_messages = [{"role": "system", "content": "sys"}]
        assert len(r.session_messages) == 1


# ---------------------------------------------------------------------------
# Multi-intent turn markers
# ---------------------------------------------------------------------------

class TestMultiIntentSession:
    def test_multi_intent_is_one_turn(self, tmp_path):
        """Multi-intent run produces exactly one turn start + end in the session stream."""
        orch = _orch(tmp_path)
        orch.session_messages = [
            LLMMessage("system", "sys"),
            LLMMessage("user", "placeholder"),
        ]

        from dbaide.agent.orchestrator import _sub_intent_from_dict

        class FakeIntent:
            def __init__(self, id, text):
                self.id = id
                self.type = "data_query"
                self.text = text
                self.language = "en"
                self.label = text.capitalize()
            def label_for(self, lang):
                return self.label

        intents = [FakeIntent("i1", "A"), FakeIntent("i2", "B")]

        # Monkeypatch _run_single to return immediately
        def fake_run_single(text, *, database="", execute=True, resume_state=None,
                            user_reply="", trace_parent="", answer_language=None,
                            skip_turn_markers=False):
            return AssistantResponse(answer=f"answer for {text}", status="completed")

        orch._run_single = fake_run_single  # type: ignore[assignment]
        orch.progress = lambda x: None

        result = orch._run_multi("do A and B", intents, database="", execute=True)
        msgs = orch.session_messages

        starts = [m for m in msgs if m.role == "user"
                  and m.content.startswith("[turn:") and ":start]" in m.content[:30]]
        ends = [m for m in msgs if m.role == "user"
                and m.content.startswith("[turn:") and ":end]" in m.content[:30]]
        assert len(starts) == 1, f"Expected 1 turn start, got {len(starts)}"
        assert len(ends) == 1, f"Expected 1 turn end, got {len(ends)}"

        # Aggregated answer should be in the stream
        assistant_msgs = [m for m in msgs if m.role == "assistant"]
        assert any("answer for A" in m.content for m in assistant_msgs)

    def test_multi_intent_no_session_skips_markers(self, tmp_path):
        """Without session_messages, no turn markers are produced."""
        orch = _orch(tmp_path)
        orch.session_messages = None

        class FakeIntent:
            def __init__(self, id, text):
                self.id = id
                self.type = "data_query"
                self.text = text
                self.language = "en"
                self.label = text
            def label_for(self, lang):
                return self.label

        intents = [FakeIntent("i1", "A")]

        def fake_run_single(text, *, database="", execute=True, resume_state=None,
                            user_reply="", trace_parent="", answer_language=None,
                            skip_turn_markers=False):
            return AssistantResponse(answer=f"answer for {text}", status="completed")

        orch._run_single = fake_run_single
        orch.progress = lambda x: None

        orch._run_multi("do A", intents, database="", execute=True)
        assert orch.session_messages is None


# ---------------------------------------------------------------------------
# Configurable parameter
# ---------------------------------------------------------------------------

class TestSessionConfig:
    def test_session_uncompressed_turns_default(self):
        cfg = ConnectionConfig(name="t", type="sqlite", path=":memory:")
        s = Session(connection=cfg)
        assert s.session_uncompressed_turns == 2

    def test_session_from_policy(self):
        from dbaide.db.policy import ResourcePolicy
        cfg = ConnectionConfig(name="t", type="sqlite", path=":memory:")
        policy = ResourcePolicy(session_uncompressed_turns=5)
        s = Session.from_policy(cfg, policy)
        assert s.session_uncompressed_turns == 5


# ---------------------------------------------------------------------------
# Three-layer compression
# ---------------------------------------------------------------------------

def _make_turn_messages(turn_num: int, *, question: str = "", answer: str = "",
                         tool_msgs: int = 6) -> list[LLMMessage]:
    """Generate realistic raw messages for a completed turn.

    Each tool result is padded to ~400 chars (~100 tokens) to produce
    realistic token counts that trigger compression with a small context budget.
    """
    q = question or f"Question for turn {turn_num}"
    a = answer or f"Answer for turn {turn_num}"
    msgs = [LLMMessage("user", f"[turn:{turn_num}:start]\n{q}")]
    # Simulate tool-call overhead with realistic-sized payloads
    for i in range(tool_msgs):
        msgs.append(LLMMessage("assistant",
            f'{{"action":"call_tool","tool":"describe_table","args":{{"table":"t{i}"}}'
            f',"thought":"exploring schema for table t{i} to understand columns and types"}}'))
        msgs.append(LLMMessage("user",
            f"[Tool result: describe_table]\n"
            f"Table: main.table_{i}\n"
            f"Columns:\n"
            f"  id INTEGER PRIMARY KEY AUTOINCREMENT\n"
            f"  user_id INTEGER NOT NULL REFERENCES users(id)\n"
            f"  amount DECIMAL(12,2) NOT NULL DEFAULT 0.00\n"
            f"  status VARCHAR(20) CHECK(status IN ('pending','paid','shipped','cancelled'))\n"
            f"  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP\n"
            f"  updated_at DATETIME\n"
            f"Indexes: idx_user_id(user_id), idx_created_at(created_at), idx_status(status)\n"
            f"Row count: {1000 * (i + 1)}\n"
            f"Foreign keys: user_id -> users.id\n"))
    msgs.append(LLMMessage("assistant", f'{{"action":"finish","answer":"{a}"}}'))
    msgs.append(LLMMessage("user", f"[turn:{turn_num}:end] Answer delivered."))
    return msgs


def _build_session_stream(*turn_specs) -> list[LLMMessage]:
    """Build a full session message stream from turn specs.

    Each spec is (turn_num, question, answer, tool_msgs).
    """
    msgs = [
        LLMMessage("system", "You are DBAide."),
        LLMMessage("user", "placeholder initial"),
    ]
    for spec in turn_specs:
        turn_num, q, a, n_tools = spec
        msgs.extend(_make_turn_messages(turn_num, question=q, answer=a, tool_msgs=n_tools))
    return msgs


class _SmallContextConfig:
    """Fake model config — context_budget() clamps to min 8000, so we set
    threshold low and generate enough test messages to exceed it."""
    context_length = 8000


class _JsonExtractorLLM(LLMClient):
    """LLM that returns valid JSON extraction for compression tests."""

    def __init__(self, answer: str = "Done."):
        self._answer = answer
        self.compress_calls: list[str] = []

    def complete_json(self, messages: list[LLMMessage], **kw) -> dict[str, Any]:
        system = messages[0].content if messages else ""
        if "tool loop" in system.lower():
            return {"action": "finish", "answer": self._answer}
        if "relevant_indices" in system:
            return {"relevant_indices": [], "reason": "mock"}
        return {}

    def complete_text(self, messages: list[LLMMessage]) -> str:
        user_msg = messages[-1].content if messages else ""
        self.compress_calls.append(user_msg[:200])
        if "TURN" in user_msg and "TO COMPRESS" in user_msg:
            return '{"question":"test q","tables":[],"executed_sqls":[{"sql":"SELECT 1","purpose":"test","result":"1 row"}],"criteria":[],"discoveries":[],"excluded":[],"answer":"test answer"}'
        return "summary"


class _FailingExtractorLLM(_JsonExtractorLLM):
    """LLM that always fails on compression calls."""

    def complete_text(self, messages: list[LLMMessage]) -> str:
        user_msg = messages[-1].content if messages else ""
        self.compress_calls.append(user_msg[:200])
        if "TURN" in user_msg and "TO COMPRESS" in user_msg:
            raise RuntimeError("LLM extraction failed")
        return "summary"


class TestThreeLayerCompression:
    def test_no_compression_under_threshold(self, tmp_path):
        """Messages under threshold are left untouched."""
        orch = _orch(tmp_path)
        orch.session.compress_threshold = 80
        msgs = _build_session_stream((1, "Q1", "A1", 2), (2, "Q2", "A2", 2))
        original_len = len(msgs)
        loop = AskAgentLoop(orch)
        loop._maybe_compress_turns(orch, msgs)
        assert len(msgs) == original_len

    def test_compress_oldest_turn_to_json(self, tmp_path):
        """When over threshold, oldest turns are compressed to JSON."""
        llm = _JsonExtractorLLM()
        orch = _orch(tmp_path, llm=llm)
        orch.model_config = _SmallContextConfig()
        orch.session.compress_threshold = 50
        orch.session.session_uncompressed_turns = 1
        msgs = _build_session_stream(
            (1, "Q1", "A1", 12),
            (2, "Q2", "A2", 12),
            (3, "Q3", "A3", 12),
        )
        loop = AskAgentLoop(orch)
        loop._maybe_compress_turns(orch, msgs)
        # Turns 1 and 2 should be compressed; turn 3 kept raw
        compressed = [m for m in msgs if m.content.startswith("[Compressed turn t")]
        assert len(compressed) >= 1
        # Compressed messages should contain JSON
        for cm in compressed:
            assert '"question"' in cm.content
        # Turn 3 should still be raw (its start marker present)
        turn3_start = any("[turn:3:start]" in m.content for m in msgs)
        assert turn3_start

    def test_compression_fallback_on_llm_failure(self, tmp_path):
        """When LLM fails, deterministic fallback from session.turns[] is used."""
        llm = _FailingExtractorLLM()
        orch = _orch(tmp_path, llm=llm)
        orch.model_config = _SmallContextConfig()
        orch.session.compress_threshold = 50
        orch.session.session_uncompressed_turns = 1
        orch.session_turns = [
            {"question": "Fallback Q", "selected_sql": "SELECT 1",
             "answer_markdown": "A1",
             "disclosed_tables": ["main.orders"],
             "clarifications": ["exclude cancelled"],
             "executed_sqls": [
                 {"sql": "SELECT COUNT(*) FROM orders", "purpose": "count", "row_count": 1},
                 {"sql": "SELECT SUM(amount) FROM orders", "purpose": "total", "row_count": 1},
             ]},
        ]
        msgs = _build_session_stream((1, "Fallback Q", "A1", 15), (2, "Q2", "A2", 15))
        loop = AskAgentLoop(orch)
        loop._maybe_compress_turns(orch, msgs)
        compressed = [m for m in msgs if m.content.startswith("[Compressed turn t")]
        assert len(compressed) >= 1
        content = compressed[0].content
        assert "Fallback Q" in content
        assert "retrieve_turn" in content
        # Fallback should include all executed_sqls from session.turns
        assert "SELECT COUNT" in content
        assert "SELECT SUM" in content

    def test_circuit_breaker_after_3_failures(self, tmp_path):
        """After 3 consecutive LLM failures, compression stops."""
        llm = _FailingExtractorLLM()
        orch = _orch(tmp_path, llm=llm)
        orch.model_config = _SmallContextConfig()
        orch.session.compress_threshold = 50
        orch.session.session_uncompressed_turns = 1
        msgs = _build_session_stream(
            (1, "Q1", "A1", 8), (2, "Q2", "A2", 8),
            (3, "Q3", "A3", 8), (4, "Q4", "A4", 8),
            (5, "Q5", "A5", 8),
        )
        loop = AskAgentLoop(orch)
        loop._maybe_compress_turns(orch, msgs)
        # Should have attempted at most 3+1 compressions (circuit break after 3 failures)
        # _compress_raw_turns processes in reversed order, so at most 3 calls before break
        assert len(llm.compress_calls) <= 4

    def test_already_compressed_turns_not_recompressed(self, tmp_path):
        """Turns already in compressed JSON format are not re-compressed."""
        llm = _JsonExtractorLLM()
        orch = _orch(tmp_path, llm=llm)
        orch.model_config = _SmallContextConfig()
        orch.session.compress_threshold = 50
        orch.session.session_uncompressed_turns = 1
        msgs = [
            LLMMessage("system", "sys"),
            LLMMessage("user", "init"),
            # Already compressed turn
            LLMMessage("user", '[Compressed turn t1 — retrieve_turn(t1) for full details]\n{"question":"Q1"}'),
            LLMMessage("user", "[turn:1:end] Answer delivered."),
            # Raw turn 2
        ]
        msgs.extend(_make_turn_messages(2, question="Q2", answer="A2", tool_msgs=12))
        # Add turn 3 (to keep raw)
        msgs.extend(_make_turn_messages(3, question="Q3", answer="A3", tool_msgs=8))

        loop = AskAgentLoop(orch)
        initial_compress_calls = len(llm.compress_calls)
        loop._maybe_compress_turns(orch, msgs)
        # The already-compressed t1 should not have triggered an LLM call
        # Only t2 should be compressed (t3 is kept raw)
        new_calls = len(llm.compress_calls) - initial_compress_calls
        assert new_calls <= 1  # at most 1 new compression call for t2

    def test_layer3_demotion(self, tmp_path):
        """When Layer 2 JSON still exceeds budget, oldest get demoted to headers."""
        llm = _JsonExtractorLLM()
        orch = _orch(tmp_path, llm=llm)
        orch.model_config = _SmallContextConfig()
        orch.session.compress_threshold = 50
        orch.session.session_uncompressed_turns = 1
        orch.session_turns = [
            {"question": f"Q{i}", "selected_sql": f"SELECT {i}", "answer_markdown": f"A{i}",
             "disclosed_tables": [f"main.t{i}"], "clarifications": []}
            for i in range(1, 6)
        ]
        msgs = _build_session_stream(
            (1, "Q1", "A1", 10), (2, "Q2", "A2", 10),
            (3, "Q3", "A3", 10), (4, "Q4", "A4", 10),
            (5, "Q5", "A5", 8),
        )
        loop = AskAgentLoop(orch)
        loop._maybe_compress_turns(orch, msgs)
        # All compressible turns should be compressed
        compressed = [m for m in msgs if "[Compressed turn t" in m.content[:25]]
        assert len(compressed) >= 1

    def test_find_compressed_turn_indices(self):
        """_find_compressed_turn_indices finds compressed messages by content prefix."""
        msgs = [
            LLMMessage("system", "sys"),
            LLMMessage("user", "init"),
            LLMMessage("user", '[Compressed turn t1 — retrieve_turn(t1) for full details]\n{"question":"Q1"}'),
            LLMMessage("user", '[Compressed turn t2 — retrieve_turn(t2) for full details]\n{"question":"Q2"}'),
            LLMMessage("user", "[turn:3:start]\nQ3"),
            LLMMessage("assistant", "A3"),
            LLMMessage("user", "[turn:3:end] Answer delivered."),
        ]
        indices = AskAgentLoop._find_compressed_turn_indices(msgs)
        assert indices == [2, 3]

    def test_phase2_demotion_finds_compressed_turns(self, tmp_path):
        """Phase 2 correctly finds and demotes compressed turns even though
        they lack [turn:N:start/end] markers."""
        orch = _orch(tmp_path)
        orch.session_turns = [
            {"question": "Q1", "disclosed_tables": ["main.t1"]},
            {"question": "Q2", "disclosed_tables": ["main.t2"]},
        ]
        # Directly test _demote_compressed_turns — Phase 2 in isolation.
        big_json = json.dumps({
            "question": "Long question", "tables": [{"name": "main.orders"}],
            "executed_sqls": [{"sql": "SELECT 1", "result": "ok"}],
            "answer": "answer text here that is long enough to be demoted",
        }, ensure_ascii=False)
        msgs = [
            LLMMessage("system", "sys"),
            LLMMessage("user", "init"),
            LLMMessage("user", f"[Compressed turn t1 — retrieve_turn(t1) for full details]\n{big_json}"),
            LLMMessage("user", f"[Compressed turn t2 — retrieve_turn(t2) for full details]\n{big_json}"),
        ]
        original_t1 = msgs[2].content
        indices = AskAgentLoop._find_compressed_turn_indices(msgs)
        assert indices == [2, 3]
        loop = AskAgentLoop(orch)
        loop._demote_compressed_turns(orch, msgs, indices, threshold=0)
        # Both should be demoted to minimal headers
        assert len(msgs[2].content) < len(original_t1)
        assert "Q1" in msgs[2].content
        # Demoted version should NOT contain executed_sqls or answer
        assert "SELECT 1" not in msgs[2].content
        assert "answer text" not in msgs[2].content

    def test_extract_turn_number(self):
        msgs = [
            LLMMessage("user", "[turn:3:start]\nWhat is X?"),
            LLMMessage("assistant", "Answer"),
            LLMMessage("user", "[turn:3:end] Answer delivered."),
        ]
        assert AskAgentLoop._extract_turn_number(msgs) == 3

    def test_extract_turn_number_from_compressed(self):
        assert AskAgentLoop._extract_turn_number_from_compressed(
            "[Compressed turn t5 — retrieve_turn(t5) for full details]\n{}"
        ) == 5

    def test_hard_truncate_backstop_converges(self, tmp_path):
        """When nothing is compressible (all turns within keep_recent) but the
        stream still exceeds budget, Phase-3 hard truncation must converge."""
        llm = _JsonExtractorLLM()
        orch = _orch(tmp_path, llm=llm)
        orch.model_config = _SmallContextConfig()
        orch.session.compress_threshold = 50
        orch.session.session_uncompressed_turns = 2
        big = "X" * 8000  # ~2000 tokens each
        msgs = [LLMMessage("system", "sys")]
        for n in (1, 2):
            msgs.append(LLMMessage("user", f"[turn:{n}:start]\n{big}"))
            msgs.append(LLMMessage("assistant", big))
            msgs.append(LLMMessage("user", f"[turn:{n}:end] done"))
        msgs.append(LLMMessage("user", "[turn:3:start]\ncurrent question"))

        loop = AskAgentLoop(orch)
        budget = loop._context_budget()
        threshold = int(budget * 50 / 100)
        loop._maybe_compress_turns(orch, msgs)
        after = sum(estimate_tokens(m.content) for m in msgs)

        assert msgs[0].content == "sys"  # system prompt preserved
        assert "current question" in msgs[-1].content  # current turn preserved
        assert after <= threshold  # converged
        assert any("truncated to fit" in m.content for m in msgs)

    def test_reported_tokens_trigger_compaction_when_estimate_underreads(self, tmp_path):
        """If the API-reported prompt_tokens exceed the threshold, compaction must
        trigger even when the local char-estimate is small (estimate under-read)."""
        llm = _JsonExtractorLLM()
        orch = _orch(tmp_path, llm=llm)
        orch.model_config = _SmallContextConfig()  # context_length floored to 8000
        orch.session.compress_threshold = 50       # threshold = 4000
        orch.session.session_uncompressed_turns = 1
        # Small messages by char-estimate, but the API says the prompt was huge.
        msgs = [LLMMessage("system", "sys")]
        for n in (1, 2):
            msgs.append(LLMMessage("user", f"[turn:{n}:start]\nq{n}"))
            msgs.append(LLMMessage("assistant", f"a{n}"))
            msgs.append(LLMMessage("user", f"[turn:{n}:end] done"))
        msgs.append(LLMMessage("user", "[turn:3:start]\ncurrent"))
        before = len(msgs)
        orch.llm.last_usage = {"prompt_tokens": 7000}  # > threshold 4000

        loop = AskAgentLoop(orch)
        # Estimate alone is tiny (well under threshold) → without the reported-token
        # floor, compaction would NOT run. With it, the old turns get compressed.
        loop._maybe_compress_turns(orch, msgs)
        assert len(msgs) < before  # compaction occurred

    def test_reported_tokens_zero_when_no_usage(self, tmp_path):
        orch = _orch(tmp_path)
        loop = AskAgentLoop(orch)
        assert loop._reported_prompt_tokens(orch) == 0
        orch.llm.last_usage = {"prompt_tokens": 1234}
        assert loop._reported_prompt_tokens(orch) == 1234

    def test_hard_truncate_noop_under_threshold(self, tmp_path):
        orch = _orch(tmp_path)
        msgs = [LLMMessage("system", "sys"), LLMMessage("user", "small"),
                LLMMessage("assistant", "ok"), LLMMessage("user", "tiny")]
        original = list(msgs)
        AskAgentLoop._hard_truncate_session(msgs, threshold=100000)
        assert msgs == original  # nothing dropped

    def test_is_already_compressed(self):
        msgs = [
            LLMMessage("user", '[Compressed turn t1 — retrieve_turn(t1) for full details]\n{"q":"x"}'),
        ]
        assert AskAgentLoop._is_already_compressed(msgs, (0, 0)) is True
        msgs2 = [
            LLMMessage("user", "[turn:1:start]\nQ"),
            LLMMessage("user", "[turn:1:end] Answer delivered."),
        ]
        assert AskAgentLoop._is_already_compressed(msgs2, (0, 1)) is False


# ---------------------------------------------------------------------------
# session_turn_prompt active_criteria injection
# ---------------------------------------------------------------------------

class TestSessionTurnPromptCriteria:
    def test_criteria_injected(self, tmp_path):
        """session_turn_prompt includes active_criteria when present."""
        orch = _orch(tmp_path)
        orch.active_criteria = ["排除 cancelled 订单", "只看北京时间"]
        orch._reset_loop_state("test", "", True)
        from dbaide.agent.loop_prompts import DecisionPromptBuilder
        builder = DecisionPromptBuilder(orch)
        from dbaide.agent.loop import LoopState
        state = LoopState(question="follow up?", database="", execute_allowed=True, answer_language="zh")
        prompt = builder.session_turn_prompt(state, 3)
        assert "排除 cancelled 订单" in prompt
        assert "只看北京时间" in prompt
        assert "Confirmed criteria" in prompt

    def test_no_criteria_when_empty(self, tmp_path):
        """session_turn_prompt omits criteria section when empty."""
        orch = _orch(tmp_path)
        orch._reset_loop_state("test", "", True)
        from dbaide.agent.loop_prompts import DecisionPromptBuilder
        builder = DecisionPromptBuilder(orch)
        from dbaide.agent.loop import LoopState
        state = LoopState(question="q?", database="", execute_allowed=True, answer_language="en")
        prompt = builder.session_turn_prompt(state, 1)
        assert "Confirmed criteria" not in prompt


# ---------------------------------------------------------------------------
# Session bootstrap: empty list triggers turn markers on first turn
# ---------------------------------------------------------------------------

class TestSessionBootstrap:
    def test_empty_list_bootstraps_turn_markers(self, tmp_path):
        """An empty session_messages list bootstraps turn markers for the first turn."""
        orch = _orch(tmp_path)
        orch.session_messages = []

        loop = AskAgentLoop(orch)
        loop.run("统计订单", session_messages=[])

        msgs = orch.session_messages
        assert msgs is not None
        starts = [m for m in msgs if m.role == "user"
                  and m.content.startswith("[turn:") and ":start]" in m.content[:30]]
        ends = [m for m in msgs if m.role == "user"
                and m.content.startswith("[turn:") and ":end]" in m.content[:30]]
        assert len(starts) == 1, f"Expected 1 start marker, got {len(starts)}"
        assert len(ends) == 1, f"Expected 1 end marker, got {len(ends)}"
        assert "[turn:1:start]" in starts[0].content
        assert "[turn:1:end]" in ends[0].content

    def test_bootstrap_then_continuity(self, tmp_path):
        """After bootstrap, second turn uses session continuity (not isolation)."""
        orch = _orch(tmp_path)
        orch.session_messages = []

        # First turn: bootstrap
        loop = AskAgentLoop(orch)
        loop.run("第一个问题", session_messages=[])

        first_msgs = list(orch.session_messages)
        assert len(first_msgs) >= 4  # system + [turn:1:start] + ... + [turn:1:end]

        # Second turn: reuse the first turn's messages
        loop2 = AskAgentLoop(orch)
        loop2.run("第二个问题", session_messages=list(first_msgs))

        second_msgs = orch.session_messages
        assert second_msgs is not None
        # Should have both turn 1 and turn 2 markers
        turn1_start = any("[turn:1:start]" in m.content for m in second_msgs if m.role == "user")
        turn2_start = any("[turn:2:start]" in m.content for m in second_msgs if m.role == "user")
        turn1_end = any("[turn:1:end]" in m.content for m in second_msgs if m.role == "user")
        turn2_end = any("[turn:2:end]" in m.content for m in second_msgs if m.role == "user")
        assert turn1_start
        assert turn1_end
        assert turn2_start
        assert turn2_end


# ---------------------------------------------------------------------------
# Multi-intent resume: skip_turn_markers on resumed sub-intent
# ---------------------------------------------------------------------------

class TestMultiIntentResume:
    def test_resume_skips_turn_markers_for_sub_intent(self, tmp_path):
        """When resuming a multi-intent plan, the resumed sub-intent should not
        emit its own turn markers (the multi wrapper owns them)."""
        orch = _orch(tmp_path)
        orch.session_messages = [
            LLMMessage("system", "sys"),
            LLMMessage("user", "placeholder"),
        ]

        calls = []
        original_run_single = orch._run_single

        def spy_run_single(*args, **kwargs):
            calls.append(kwargs.get("skip_turn_markers", "NOT_SET"))
            return AssistantResponse(answer="resumed", status="completed")

        orch._run_single = spy_run_single
        orch.progress = lambda x: None

        resume_state = {
            "multi": {
                "question": "do A and B",
                "done": [],
                "paused": {"id": "i1", "type": "data_query", "text": "A", "language": "en"},
                "remaining": [{"id": "i2", "type": "data_query", "text": "B", "language": "en"}],
            },
            "question": "do A and B",
        }
        orch.run("do A and B", database="", execute=True,
                 resume_state=resume_state, user_reply="yes")

        # First call should have skip_turn_markers=True (multi resume)
        assert calls[0] is True, f"Expected skip_turn_markers=True for resumed sub-intent, got {calls[0]}"
