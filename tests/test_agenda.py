import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.toolkit import build_tool_registry
from dbaide.llm import LLMClient
from dbaide.models import AssistantResponse, ConnectionConfig
from dbaide.session import Session
from dbaide.tools.registry import ToolContext


def _orch(tmp_path):
    db = tmp_path / "agenda.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE orders(id INTEGER PRIMARY KEY, amount REAL, created_at TEXT);
        INSERT INTO orders VALUES (1, 10.0, '2026-06-01'), (2, 20.0, '2026-06-02');
        """
    )
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    return AskOrchestrator(build_adapter(cfg), Session(connection=cfg))


def test_update_agenda_tool_normalizes_and_reuses_ids(tmp_path):
    orch = _orch(tmp_path)
    orch._reset_loop_state("q", "", True)
    registry = build_tool_registry(orch)

    first = registry.invoke("update_agenda", {
        "items": [
            {"title": "Inspect schema", "status": "in_progress", "kind": "schema"},
            {"title": "Write query", "status": "pending", "kind": "sql"},
        ]
    }, ToolContext())
    assert first.ok
    ids = [item.id for item in orch.run_state.agenda]
    assert ids == ["task:1", "task:2"]

    second = registry.invoke("update_agenda", {
        "explanation": "Schema done; query now active",
        "items": [
            {"title": "Inspect schema", "status": "done", "kind": "schema"},
            {"title": "Write query", "status": "in_progress", "kind": "sql"},
        ]
    }, ToolContext())
    assert second.ok
    assert [item.id for item in orch.run_state.agenda] == ids
    assert orch.run_state.agenda[0].status == "done"
    assert orch.run_state.agenda[1].status == "in_progress"
    # The tool carries the structured agenda in meta → trace metadata → persisted trace,
    # so the conversation's agenda panel can rebuild after finalize/reload.
    assert isinstance(second.meta, dict) and "agenda" in second.meta
    assert second.meta["agenda"]["items"][0]["title"] == "Inspect schema"


def test_latest_agenda_reads_live_and_persisted_shapes():
    """The agenda panel parser must handle the live event (structured under result_data)
    AND the persisted/reloaded event (structured under metadata; result_data is flattened
    to a preview string). The metadata path regressed the panel after a turn finalized."""
    from dbaide.agent.agenda import latest_agenda_from_events
    items = [
        {"id": "t1", "title": "Inspect schema", "status": "done"},
        {"id": "t2", "title": "Write query", "status": "in_progress"},
    ]
    live = {"stage": "update_agenda", "result_data": {"agenda": {"items": items}}}
    persisted = {
        "stage": "update_agenda",
        "output_preview": "{'agenda': {'items': [...]}}",  # truncated string, structure lost
        "metadata": {"agenda": {"items": items}},
    }
    assert [i.title for i in latest_agenda_from_events([live])] == ["Inspect schema", "Write query"]
    assert [i.status for i in latest_agenda_from_events([persisted])] == ["done", "in_progress"]


def test_agenda_from_dict_tolerates_model_field_drift():
    """Real model output uses `task` instead of `title` (it confuses the subagent tool's
    field) and localized status words. Previously every item was silently dropped, leaving
    an empty agenda and no panel. The parser now accepts the synonyms."""
    from dbaide.agent.agenda import agenda_from_dict
    items = [
        {"task": "查询公司在职员工数", "status": "待开始"},
        {"task": "查询公司离职员工数", "status": "未开始"},
        {"task": "查询是否有离职又回来的员工", "status": "进行中"},
        {"task": "查询王煦的入职时间", "status": "已完成"},
    ]
    parsed = agenda_from_dict(items)
    assert [i.title for i in parsed] == [it["task"] for it in items]
    assert [i.status for i in parsed] == ["pending", "pending", "in_progress", "done"]
    # English synonyms (name/text + todo/doing/finished) also resolve
    alt = agenda_from_dict([
        {"name": "scan", "status": "todo"},
        {"text": "run", "status": "doing"},
        {"title": "verify", "status": "finished"},
    ])
    assert [i.status for i in alt] == ["pending", "in_progress", "done"]


class _AgendaLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, messages, *, schema_hint=""):
        script = [
            {
                "action": "call_tool",
                "tool": "update_agenda",
                "args": {
                    "items": [
                        {"title": "Inspect schema", "status": "done", "kind": "schema"},
                        {"title": "Validate result", "status": "in_progress", "kind": "verify"},
                    ]
                },
            },
            {"action": "finish", "answer": "too early"},
            {
                "action": "call_tool",
                "tool": "update_agenda",
                "args": {
                    "items": [
                        {"title": "Inspect schema", "status": "done", "kind": "schema"},
                        {"title": "Validate result", "status": "done", "kind": "verify"},
                    ]
                },
            },
            {"action": "finish", "answer": "final answer"},
        ]
        item = script[min(self.calls, len(script) - 1)]
        self.calls += 1
        return item

    def complete_text(self, messages):
        return "OK"


def test_finish_requires_agenda_completion(tmp_path):
    orch = _orch(tmp_path)
    orch.llm = _AgendaLLM()
    resp = AskAgentLoop(orch).run("validate something", execute=False)

    assert resp.answer == "final answer"
    assert orch.llm.calls == 4
    assert all(item.status == "done" for item in orch.run_state.agenda)


def test_run_subagent_injects_context_refs_and_allowed_tools(tmp_path, monkeypatch):
    orch = _orch(tmp_path)
    orch._reset_loop_state("compare orders", "main", True)
    orch.run_state.sql = "SELECT COUNT(*) FROM orders"
    orch.run_state.schemas = {"main.orders": []}
    orch.run_state.schema_db = {"main.orders": "main"}

    captured: dict[str, object] = {}

    def fake_run(self, question, **kwargs):
        captured["allowed"] = set(self.allowed_tool_names)
        captured["question"] = question
        return AssistantResponse(answer="- verified row count", sql="SELECT 1")

    monkeypatch.setattr("dbaide.agent.loop.AskAgentLoop.run", fake_run)

    result = build_tool_registry(orch).invoke("run_subagent", {
        "task": "verify the current query shape",
        "context": "Focus on whether the current SQL can answer the question.",
        "context_refs": ["current_sql", "current_schema"],
        "deliverables": ["verified_facts", "candidate_sql"],
        "allowed_tools": ["describe_table", "execute_sql", "update_agenda"],
        "execute": False,
    }, ToolContext())

    assert result.ok
    assert captured["allowed"] == {"describe_table", "execute_sql", "update_agenda"}
    question = str(captured["question"] or "")
    assert "Expected deliverables: verified_facts, candidate_sql" in question
    assert "SELECT COUNT(*) FROM orders" in question
    assert "[current_schema]" in question
