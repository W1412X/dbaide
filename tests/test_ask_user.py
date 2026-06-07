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
    assert orch.run_state.pending_question == "Pick one"
    assert orch.run_state.pending_options == ["A", "B"]


def test_ask_user_has_no_keyword_gate(tmp_path):
    db = tmp_path / "app.db"
    sqlite3.connect(db).execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, shipping_country TEXT)")
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), ClarifyMockLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("compare refunds", "", True)

    result = registry.invoke(
        "ask_user",
        {
            "question": (
                "为了准确比对受投退款统计表和订单源数据，我需要确认以下关键点：\n"
                "1. 国家字段来源：统计表包含 country 字段，但订单明细表没有直接的国家字段，"
                "订单主表是否包含 shipping_country 或类似字段？\n"
                "2. 受投日期定义：统计表使用北京日（UTC+8），而订单明细中的 delivered_at 是 UTC 时间戳，"
                "是否应将 delivered_at 转换为北京日期来匹配 delivered_date？"
            ),
            "options": ["是", "否"],
        },
        ToolContext(),
    )

    assert result.ok
    assert result.data["pending"] is True
    assert "shipping_country" in orch.run_state.pending_question
    assert orch.run_state.pending_options == ["是", "否"]


def test_ask_user_allows_irreducible_business_choice(tmp_path):
    db = tmp_path / "app.db"
    sqlite3.connect(db).execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, created_at TEXT)")
    conn = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(conn), Session(connection=conn), ClarifyMockLLM())
    registry = build_tool_registry(orch)
    orch._reset_loop_state("order stats", "", True)

    result = registry.invoke(
        "ask_user",
        {
            "question": "结果需要按日展示还是按月展示？",
            "options": ["按日", "按月"],
        },
        ToolContext(),
    )

    assert result.ok
    assert orch.run_state.pending_question == "结果需要按日展示还是按月展示？"
    assert orch.run_state.pending_options == ["按日", "按月"]


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
    orch.run_state.sql = "SELECT 1"
    orch.run_state.pending_question = "ignored on restore"
    snapshot = dump_loop_state(orch, transcript=["Tool `x` → ok"], execute_allowed=False)
    restore_loop_state(orch, snapshot)
    assert orch.run_state.question == "q"
    assert orch.run_state.sql == "SELECT 1"
    assert orch.run_state.execute_allowed is False
