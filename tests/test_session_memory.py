"""Session memory: prior turns + active criteria carry across turns in a chat.

Progressive disclosure — by default only Q/A/SQL summaries enter the prompt; the
agent fetches clarifications / full SQL / earlier turns via retrieve_turn /
list_earlier_turns when it needs them. The user-confirmed criteria from earlier
turns are auto-carried as binding context so a follow-up doesn't lose 口径.
"""
import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.loop_prompts import DecisionPromptBuilder
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext


class _MockLLM(LLMClient):
    def complete_json(self, messages, *, schema_hint=""):
        return {}

    def complete_text(self, messages):
        return ""


def _orch(tmp_path):
    db = tmp_path / "s.db"
    sqlite3.connect(db).execute("CREATE TABLE orders (id INTEGER PRIMARY KEY)")
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    return AskOrchestrator(build_adapter(conn), Session(connection=conn), _MockLLM())


def _state(question="q"):
    class _S:
        pass
    s = _S()
    s.question = question
    s.database = ""
    s.execute_allowed = True
    s.answer_language = "en"
    return s


def test_prior_turns_render_in_user_prompt_with_qa_and_sql(tmp_path):
    orch = _orch(tmp_path)
    orch._reset_loop_state("now follow up", "", True)
    orch.session_turns = [
        {"question": "5月份妥投数量",
         "answer_markdown": "May delivered count: 1,234 orders.",
         "selected_sql": "SELECT count(*) FROM orders WHERE delivered_at >= '2025-05-01'",
         "status": "completed", "clarifications": ["按北京时间"]},
        {"question": "按城市拆分",
         "answer_markdown": "Top: NYC 412, Tokyo 380, SF 220",
         "selected_sql": "SELECT city, count(*) FROM orders GROUP BY city",
         "status": "completed", "clarifications": []},
    ]
    prompt = DecisionPromptBuilder(orch).initial_user_prompt(_state("now follow up"))

    assert "[Prior turns in this session]" in prompt
    # Both turns are visible (window=3, total=2), with Q/A/SQL summaries.
    assert "t1: Q: 5月份妥投数量" in prompt
    assert "May delivered count: 1,234 orders" in prompt
    assert "WHERE delivered_at >= '2025-05-01'" in prompt
    assert "t2: Q: 按城市拆分" in prompt
    # Tools the model can call for more are advertised in the section header.
    assert "retrieve_turn" in prompt
    assert "list_earlier_turns" in prompt


def test_prior_turns_signal_earlier_when_window_overflows(tmp_path):
    orch = _orch(tmp_path)
    orch._reset_loop_state("q", "", True)
    orch.session_turns = [
        {"question": f"q{i}", "answer_markdown": f"a{i}", "selected_sql": "", "status": "completed"}
        for i in range(7)
    ]
    prompt = DecisionPromptBuilder(orch).initial_user_prompt(_state("q"))

    # Window is 3 of 7 — the prompt must say so AND name how to page back.
    assert "showing 3 of 7" in prompt
    assert "+4 earlier turn(s)" in prompt
    # And the visible window is the LATEST three (t5..t7), not the oldest.
    assert "t5:" in prompt and "t6:" in prompt and "t7:" in prompt
    assert "t1:" not in prompt and "t2:" not in prompt


def test_active_criteria_carry_forward_as_binding_clarifications(tmp_path):
    """L2 binding: when the next turn starts, every confirmed criterion from
    earlier turns in the session is seeded into run_state.clarifications, which
    the SQL writer renders verbatim in its [Business criteria] block."""
    orch = _orch(tmp_path)
    orch.active_criteria = ["按北京时间", "仅 paid 状态"]
    orch._reset_loop_state("新问题", "", True)
    assert orch.run_state.clarifications == ["按北京时间", "仅 paid 状态"]


def test_retrieve_turn_returns_full_fields_by_default(tmp_path):
    orch = _orch(tmp_path)
    orch._reset_loop_state("q", "", True)
    orch.session_turns = [
        {"question": "5月妥投", "answer_markdown": "1234 orders.",
         "selected_sql": "SELECT count(*) FROM orders",
         "status": "completed", "clarifications": ["按北京时间"],
         "disclosed_tables": ["main.orders"]},
    ]
    reg = build_tool_registry(orch)
    r = reg.invoke("retrieve_turn", {"turn_id": "t1"}, ToolContext())
    assert r.ok
    d = r.data
    assert d["turn_id"] == "t1"
    assert d["question"] == "5月妥投"
    assert d["clarifications"] == ["按北京时间"]
    assert d["selected_sql"].startswith("SELECT count(*)")
    assert d["answer_markdown"] == "1234 orders."
    assert d["disclosed_tables"] == ["main.orders"]


def test_retrieve_turn_filters_by_include(tmp_path):
    orch = _orch(tmp_path)
    orch._reset_loop_state("q", "", True)
    orch.session_turns = [
        {"question": "q1", "answer_markdown": "a1", "selected_sql": "SELECT 1",
         "status": "completed", "clarifications": ["c1"], "disclosed_tables": ["t"]},
    ]
    reg = build_tool_registry(orch)
    r = reg.invoke("retrieve_turn", {"turn_id": "t1", "include": ["clarifications", "sql"]},
                   ToolContext())
    assert r.ok
    # Only the requested fields are returned (plus the metadata stubs).
    assert "clarifications" in r.data and "selected_sql" in r.data
    assert "answer_markdown" not in r.data
    assert "disclosed_tables" not in r.data


def test_retrieve_turn_rejects_unknown_id_and_bad_include(tmp_path):
    orch = _orch(tmp_path)
    orch._reset_loop_state("q", "", True)
    orch.session_turns = [{"question": "q1", "answer_markdown": "a1", "status": "completed"}]
    reg = build_tool_registry(orch)
    bad_id = reg.invoke("retrieve_turn", {"turn_id": "t99"}, ToolContext())
    assert not bad_id.ok and "unknown turn_id" in bad_id.error.message
    bad_inc = reg.invoke("retrieve_turn", {"turn_id": "t1", "include": ["bogus"]}, ToolContext())
    assert not bad_inc.ok and "unknown include field" in bad_inc.error.message


def test_list_earlier_turns_pages_and_signals_more(tmp_path):
    orch = _orch(tmp_path)
    orch._reset_loop_state("q", "", True)
    orch.session_turns = [
        {"question": f"Q{i}", "answer_markdown": f"A{i} answer text",
         "selected_sql": "", "status": "completed"}
        for i in range(8)
    ]
    reg = build_tool_registry(orch)
    r = reg.invoke("list_earlier_turns", {"offset": 0, "limit": 3}, ToolContext())
    assert r.ok
    assert r.data["total"] == 8
    assert r.data["more"] is True
    ids = [t["turn_id"] for t in r.data["turns"]]
    assert ids == ["t1", "t2", "t3"]
    # Each row carries question + one-line answer summary (no full markdown).
    assert r.data["turns"][0]["question"] == "Q0"
    assert "A0" in r.data["turns"][0]["answer_summary"]


def test_both_session_tools_are_batchable_and_in_loop_set():
    from dbaide.agent.loop import BATCHABLE_TOOLS
    from dbaide.agent.toolkit import LOOP_DECISION_TOOL_NAMES

    for name in ("retrieve_turn", "list_earlier_turns"):
        assert name in BATCHABLE_TOOLS, f"{name} should be batchable (read-only, no pause)"
        assert name in LOOP_DECISION_TOOL_NAMES, f"{name} should be exposed to the loop LLM"


def test_prior_turns_header_hint_uses_offset_zero(tmp_path):
    """The [Prior turns] header should suggest offset=0 for older turns, not
    offset=window_size (which overlaps with the visible window)."""
    orch = _orch(tmp_path)
    orch._reset_loop_state("q", "", True)
    orch.session_turns = [
        {"question": f"q{i}", "answer_markdown": f"a{i}", "selected_sql": "",
         "status": "completed"}
        for i in range(5)
    ]
    prompt = DecisionPromptBuilder(orch).initial_user_prompt(_state("q"))
    # Should suggest offset=0, NOT offset=<window_size>
    assert "list_earlier_turns(offset=0)" in prompt
    assert "list_earlier_turns(offset=3)" not in prompt


def test_retrieve_turn_works_with_empty_optional_fields(tmp_path):
    """Turns persisted before session memory feature have no clarifications or
    disclosed_tables fields — the tools should handle None gracefully."""
    orch = _orch(tmp_path)
    orch._reset_loop_state("q", "", True)
    orch.session_turns = [
        {"question": "old question", "answer_markdown": "old answer",
         "selected_sql": "", "status": "completed"},
        # Note: no clarifications, no disclosed_tables keys at all
    ]
    reg = build_tool_registry(orch)
    r = reg.invoke("retrieve_turn", {"turn_id": "t1"}, ToolContext())
    assert r.ok
    assert r.data["clarifications"] == []
    assert r.data["disclosed_tables"] == []


def test_list_earlier_turns_clamps_negative_offset_and_zero_limit(tmp_path):
    """Boundary: negative offset → 0; limit=0 → 1."""
    orch = _orch(tmp_path)
    orch._reset_loop_state("q", "", True)
    orch.session_turns = [
        {"question": "q1", "answer_markdown": "a1", "status": "completed"},
    ]
    reg = build_tool_registry(orch)
    r = reg.invoke("list_earlier_turns", {"offset": -5, "limit": 0}, ToolContext())
    assert r.ok
    assert r.data["total"] == 1
    assert len(r.data["turns"]) == 1  # limit clamped to 1


def test_no_prior_turns_block_when_session_empty(tmp_path):
    """First turn in a session: no session_turns → no [Prior turns] section."""
    orch = _orch(tmp_path)
    orch._reset_loop_state("first question", "", True)
    orch.session_turns = []
    prompt = DecisionPromptBuilder(orch).initial_user_prompt(_state("first question"))
    assert "[Prior turns in this session]" not in prompt


def test_active_criteria_not_seeded_when_empty(tmp_path):
    """If there are no active criteria, clarifications should start empty."""
    orch = _orch(tmp_path)
    orch.active_criteria = []
    orch._reset_loop_state("q", "", True)
    assert orch.run_state.clarifications == []
