import json
import sqlite3
from typing import Any

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop, _fmt_profile, _fmt_sql_result, _fmt_subagent
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.run_state import RunState
from dbaide.agent.toolkit.subagent_tools import _merge_child_state
from dbaide.llm import LLMClient, LLMMessage
from dbaide.mcp_server import handle_execute_sql
from dbaide.models import AssistantResponse, ConnectionConfig, QueryResult
from dbaide.session import Session


def test_sql_result_formatter_truncates_cells_not_whole_rows():
    data = {
        "sql": "SELECT id, body, tail FROM docs",
        "columns": ["id", "body", "tail"],
        "rows": [
            {"id": 1, "body": "x" * 2000, "tail": "kept-1"},
            {"id": 2, "body": "short", "tail": "kept-2"},
        ],
        "row_count": 2,
        "truncated": False,
    }

    text = _fmt_sql_result(data)

    assert "kept-1" in text
    assert "kept-2" in text
    assert "cell truncated" in text
    assert '"id": 2' in text


def test_mcp_execute_sql_returns_bounded_row_preview(monkeypatch):
    class FakeQuery:
        def execute_sql(self, sql, *, database="", limit=100, timeout_seconds=None):
            return QueryResult(
                columns=["id", "payload", "status"],
                rows=[{"id": 1, "payload": "p" * 3000, "status": "ok"}],
                row_count=1,
                sql=sql,
                elapsed_ms=1.25,
            )

    class FakeCtx:
        def get(self, conn):
            return None, None, FakeQuery(), None

    monkeypatch.setattr("dbaide.mcp_server._ctx", FakeCtx())

    result = handle_execute_sql({"sql": "SELECT * FROM docs", "limit": 100})
    payload = json.loads(result["content"][0]["text"])

    assert payload["rows"][0]["status"] == "ok"
    assert "cell truncated" in payload["rows"][0]["payload"]
    assert payload["row_preview"]["cell_truncated"] is True


def test_column_stats_formatter_uses_columns_payload():
    text = _fmt_profile({
        "table": "orders",
        "database": "main",
        "columns": [
            {"column": "status", "stats": {"distinct_count": 2, "top_values": [{"value": "paid", "count": 10}]}},
            {"column": "amount", "stats": {"min": 1, "max": 99, "null_rate": 0.0}},
        ],
    })

    assert "status: distinct=2" in text
    assert "top_values" in text
    assert "amount: null_rate=0.0, min=1, max=99" in text


def test_subagent_formatter_surfaces_preview_state_and_artifacts():
    text = _fmt_subagent({
        "task": "inspect large text",
        "status": "wait_user",
        "answer": "Need confirmation {{chart:2}}",
        "result_preview": [{"id": 1, "body": "x... [cell truncated]"}],
        "row_preview": {
            "row_preview_truncated": True,
            "rows_previewed": 1,
            "rows_returned": 10,
            "cell_truncated": True,
            "truncated_cells": 1,
        },
        "charts": [{"chart_id": "chart:2"}],
        "executed_sqls": [{"artifact_id": "sql:2", "purpose": "probe", "sql": "SELECT 1"}],
        "pending_question": "Continue?",
        "pending_options": ["yes", "no"],
    })

    assert "showing 1 of 10 returned rows" in text
    assert "1 cell(s) truncated" in text
    assert "Charts: chart:2" in text
    assert "sql:2 (probe): SELECT 1" in text
    assert "Pending question: Continue?" in text


class _SubagentMock(LLMClient):
    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "compressed"

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict[str, Any]:
        system = messages[0].content if messages else ""
        if "relevant_indices" in system:
            return {"relevant_indices": [0]}
        if "operating in a tool loop" in system:
            all_text = "\n".join(m.content for m in messages)
            if "Tool result: run_subagent" in all_text:
                return {"action": "finish", "answer": "Child evidence received."}
            is_child = "Subagent: inspect docs count" in all_text
            if is_child:
                if "Tool result: generate_sql" not in all_text:
                    return {
                        "action": "call_tool",
                        "tool": "generate_sql",
                        "args": {"question": "count docs", "table": "docs"},
                        "thought": "Generate child SQL",
                    }
                if "Tool result: execute_sql" not in all_text:
                    return {
                        "action": "call_tool",
                        "tool": "execute_sql",
                        "args": {"purpose": "count"},
                        "thought": "Execute child SQL",
                    }
                return {"action": "finish", "answer": "Docs count checked."}
            return {
                "action": "call_tool",
                "tool": "run_subagent",
                "args": {"task": "Subagent: inspect docs count", "context": "Use docs table"},
                "thought": "Delegate independent check",
            }
            return {"action": "finish", "answer": "Child evidence received."}
        if "generate safe read-only SQL" in system:
            return {"sql": "SELECT COUNT(*) AS n FROM docs", "rationale": "count docs", "confidence": 0.9}
        return {}


def test_agent_can_delegate_to_scoped_subagent(tmp_path):
    db = tmp_path / "subagent.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE docs (id INTEGER PRIMARY KEY, body TEXT); INSERT INTO docs VALUES (1, 'a');")
    conn.commit()
    conn.close()
    cfg = ConnectionConfig(name="local", type="sqlite", path=str(db))
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), _SubagentMock())

    response = AskAgentLoop(orch).run("Use a child agent to check docs", execute=True)

    assert response.answer == "Child evidence received."
    assert any(item.get("tool") == "execute_sql" for item in orch.run_state.executed_sqls)


def test_subagent_merge_preserves_parent_state_without_id_collisions():
    class Holder:
        def __init__(self) -> None:
            self.run_state = RunState()

    parent = Holder()
    child = Holder()
    parent.run_state.charts = [{"chart_id": "chart:1", "title": "parent"}]
    parent.run_state.executed_sqls = [{"index": 1, "sql": "SELECT 1", "purpose": "parent", "database": "", "tool": "execute_sql", "artifact_id": "sql:1"}]
    child.run_state.charts = [{"chart_id": "chart:1", "title": "child"}]
    child.run_state.clarifications = ["Use paid orders only"]
    child.run_state.executed_sqls = [{"index": 1, "sql": "SELECT 2", "purpose": "child", "database": "", "tool": "execute_sql", "artifact_id": "sql:1"}]
    response = AssistantResponse(
        answer="Child chart: {{chart:1}}",
        sql="SELECT 2",
        charts=[{"chart_id": "chart:1", "title": "child"}],
        executed_sqls=list(child.run_state.executed_sqls),
    )

    _merge_child_state(parent, child, response)

    assert [chart["chart_id"] for chart in parent.run_state.charts] == ["chart:1", "chart:2"]
    assert response.answer == "Child chart: {{chart:2}}"
    assert response.charts[0]["chart_id"] == "chart:2"
    assert parent.run_state.clarifications == ["Use paid orders only"]
    assert [item["index"] for item in parent.run_state.executed_sqls] == [1, 2]
