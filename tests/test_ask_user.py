import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop
from dbaide.agent.loop_state import dump_loop_state, restore_loop_state
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext


class ClarifyMockLLM(LLMClient):
    """First call ask_user; after resume, finish."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict:
        self.calls += 1
        system = messages[0].content if messages else ""
        user = messages[-1].content if messages else ""
        if "tool loop" not in system.lower():
            return {}
        if "User reply:" in user or "User reply:" in "\n".join(m.content for m in messages):
            return {"action": "finish", "answer": "Grouped by day as requested."}
        return {
            "action": "call_tool",
            "tool": "ask_user",
            "args": {
                "question": "Should results be grouped by day or by month?",
                "options": ["By day", "By month"],
            },
            "thought": "Need time grain",
        }

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "ok"


def test_ask_user_tool_sets_pending(tmp_path):
    db = tmp_path / "app.db"
    sqlite3.connect(db).execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, created_at TEXT)")
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), ClarifyMockLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("stats", "", True)
    result = registry.invoke(
        "ask_user",
        {"question": "Pick one", "options": ["A", "B"]},
        ToolContext(),
    )
    assert result.ok
    assert result.data["pending"] is True
    assert orch._loop_pending_question == "Pick one"
    assert orch._loop_pending_options == ["A", "B"]


def test_loop_pauses_on_ask_user_and_resumes(tmp_path):
    db = tmp_path / "app.db"
    sqlite3.connect(db).execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, created_at TEXT)")
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    llm = ClarifyMockLLM()
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), llm)
    loop = AskAgentLoop(orch)

    paused = loop.run("order stats", execute=False)
    assert paused is not None
    assert paused.status == "wait_user"
    assert paused.pending_question.startswith("Should results")
    assert paused.resume_state is not None

    resumed = loop.run(
        "order stats",
        execute=False,
        resume_state=paused.resume_state,
        user_reply="By day",
    )
    assert resumed is not None
    assert resumed.status == "completed"
    assert "Grouped by day" in resumed.answer


def test_loop_state_roundtrip(tmp_path):
    db = tmp_path / "app.db"
    sqlite3.connect(db).execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, created_at TEXT)")
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), ClarifyMockLLM())
    orch._reset_loop_state("q", "main", True)
    orch._loop_sql = "SELECT 1"
    orch._loop_pending_question = "ignored on restore"
    snapshot = dump_loop_state(orch, transcript=["Tool `x` → ok"], execute_allowed=False)
    restore_loop_state(orch, snapshot)
    assert orch._loop_question == "q"
    assert orch._loop_sql == "SELECT 1"
    assert orch._loop_execute_allowed is False
