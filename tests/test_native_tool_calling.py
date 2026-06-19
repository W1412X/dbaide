"""Native function/tool-calling: capability-gated path that lets the provider emit
well-formed tool calls (translated into the existing internal decision dict), with
automatic fallback to the JSON decision protocol when the endpoint rejects tools."""

from __future__ import annotations

import io
import json
import sqlite3
import urllib.error
from typing import Any

import pytest

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop, _tool_spec_to_function
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.llm import LLMClient, LLMMessage, OpenAICompatibleClient, ToolsUnsupported
from dbaide.models import ConnectionConfig, ModelConfig
from dbaide.session import Session


def _client(tool_calling="auto"):
    return OpenAICompatibleClient(ModelConfig(
        provider="openai_compatible", base_url="http://x/v1", api_key="k", model="m",
        tool_calling=tool_calling))


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def _patch_urlopen(monkeypatch, payload):
    def fake(req, *a, **k):
        return _Resp(json.dumps(payload).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake)


# ── complete_with_tools wire parsing ─────────────────────────────────────────

def test_complete_with_tools_parses_tool_calls(monkeypatch):
    _patch_urlopen(monkeypatch, {
        "choices": [{"message": {"content": None, "tool_calls": [
            {"function": {"name": "describe_table", "arguments": '{"table":"orders"}'}},
        ]}}],
        "usage": {"prompt_tokens": 10},
    })
    result = _client().complete_with_tools([LLMMessage("user", "q")], tools=[])
    assert result["content"] is None
    assert result["tool_calls"] == [{"name": "describe_table", "arguments": {"table": "orders"}}]


def test_complete_with_tools_parses_content_finish(monkeypatch):
    _patch_urlopen(monkeypatch, {"choices": [{"message": {"content": "the answer"}}]})
    result = _client().complete_with_tools([LLMMessage("user", "q")], tools=[])
    assert result["content"] == "the answer"
    assert result["tool_calls"] == []


def test_complete_with_tools_tolerates_dict_arguments(monkeypatch):
    # Some endpoints return arguments already as an object, not a JSON string.
    _patch_urlopen(monkeypatch, {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "list_tables", "arguments": {"database": "main"}}},
    ]}}]})
    result = _client().complete_with_tools([LLMMessage("user", "q")], tools=[])
    assert result["tool_calls"][0]["arguments"] == {"database": "main"}


def test_complete_with_tools_raises_unsupported_on_400(monkeypatch):
    def fake(req, *a, **k):
        raise urllib.error.HTTPError("u", 400, "no tools", {}, io.BytesIO(b"tools not supported"))
    monkeypatch.setattr("urllib.request.urlopen", fake)
    with pytest.raises(ToolsUnsupported):
        _client().complete_with_tools([LLMMessage("user", "q")], tools=[])


def test_supports_tool_calling_honors_off():
    assert _client("auto").supports_tool_calling() is True
    assert _client("on").supports_tool_calling() is True
    assert _client("off").supports_tool_calling() is False


# ── tool-spec → function-schema mapping ──────────────────────────────────────

def test_tool_spec_to_function_schema():
    from dbaide.tools.specs import ToolSpec
    spec = ToolSpec(name="execute_sql", description="run sql", input_schema={
        "sql": {"type": "string", "required": True, "description": "the query"},
        "limit": {"type": "integer", "default": 100},
        "cols": {"type": "list[string]"},      # dbaide's own type-string form
        "scope": {"type": "dict"},
        "items": {"type": "list[object]"},
    })
    fn = _tool_spec_to_function(spec)
    assert fn["type"] == "function"
    assert fn["function"]["name"] == "execute_sql"
    params = fn["function"]["parameters"]
    assert params["properties"]["sql"] == {"type": "string", "description": "the query"}
    assert params["properties"]["limit"]["type"] == "integer"
    assert params["properties"]["cols"] == {"type": "array", "items": {"type": "string"}}
    assert params["properties"]["scope"] == {"type": "object"}
    assert params["properties"]["items"] == {"type": "array", "items": {"type": "object"}}
    assert params["required"] == ["sql"]


# ── _native_decide translation + loop integration ────────────────────────────

class _ToolCallLLM(LLMClient):
    """A tool-capable mock: returns a scripted sequence of native tool-call results."""
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def supports_tool_calling(self):
        return True

    def complete_with_tools(self, messages, tools):
        item = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return item


def _orch(tmp_path):
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE orders(id int, amt real); INSERT INTO orders VALUES (1,10),(2,20);")
    c.commit(); c.close()
    conn = ConnectionConfig(name="shop", type="sqlite", path=str(db))
    return AskOrchestrator(build_adapter(conn), Session(connection=conn))


def test_native_decide_single_tool_call(tmp_path):
    orch = _orch(tmp_path)
    orch.llm = _ToolCallLLM([{"content": None, "tool_calls": [
        {"name": "execute_sql", "arguments": {"sql": "SELECT 1"}}]}])
    loop = AskAgentLoop(orch)
    decision = loop._native_decide([LLMMessage("user", "q")])
    assert decision == {"action": "call_tool", "tool": "execute_sql", "args": {"sql": "SELECT 1"}, "thought": ""}


def test_native_decide_parallel_tool_calls(tmp_path):
    orch = _orch(tmp_path)
    orch.llm = _ToolCallLLM([{"tool_calls": [
        {"name": "describe_table", "arguments": {"table": "orders"}},
        {"name": "list_tables", "arguments": {}}]}])
    loop = AskAgentLoop(orch)
    decision = loop._native_decide([LLMMessage("user", "q")])
    assert decision["action"] == "call_tools"
    assert [c["tool"] for c in decision["calls"]] == ["describe_table", "list_tables"]


def test_native_decide_captures_reasoning_content_as_thought(tmp_path):
    # Providers often return reasoning in `content` alongside the tool call;
    # it must survive as the decision's thought (trace + thought_trace parity).
    orch = _orch(tmp_path)
    orch.llm = _ToolCallLLM([{"content": "  Need the row count first.  ", "tool_calls": [
        {"name": "execute_sql", "arguments": {"sql": "SELECT COUNT(*) FROM orders"}}]}])
    loop = AskAgentLoop(orch)
    decision = loop._native_decide([LLMMessage("user", "q")])
    assert decision["thought"] == "Need the row count first."

    # ...and for parallel calls too.
    orch.llm = _ToolCallLLM([{"content": "Check both tables.", "tool_calls": [
        {"name": "describe_table", "arguments": {"table": "orders"}},
        {"name": "list_tables", "arguments": {}}]}])
    loop = AskAgentLoop(orch)
    decision = loop._native_decide([LLMMessage("user", "q")])
    assert decision["action"] == "call_tools"
    assert decision["thought"] == "Check both tables."


def test_native_decide_content_is_finish(tmp_path):
    orch = _orch(tmp_path)
    orch.llm = _ToolCallLLM([{"content": "final answer", "tool_calls": []}])
    loop = AskAgentLoop(orch)
    assert loop._native_decide([LLMMessage("user", "q")]) == {"action": "finish", "answer": "final answer"}


def test_native_decide_unknown_tool_falls_back(tmp_path):
    orch = _orch(tmp_path)
    orch.llm = _ToolCallLLM([{"tool_calls": [{"name": "made_up_tool", "arguments": {}}]}])
    loop = AskAgentLoop(orch)
    assert loop._native_decide([LLMMessage("user", "q")]) is None  # → JSON protocol


def test_end_to_end_native_loop_runs(tmp_path):
    """A tool-capable mock drives the whole loop natively: execute_sql then finish.
    Proves native decisions flow through the existing dispatch + message stream."""
    orch = _orch(tmp_path)
    orch.llm = _ToolCallLLM([
        {"tool_calls": [{"name": "execute_sql", "arguments": {"sql": "SELECT SUM(amt) AS total FROM orders"}}]},
        {"content": "Total is 30.", "tool_calls": []},
    ])
    resp = AskAgentLoop(orch).run("total amount", database="", execute=True)
    assert "30" in (resp.answer or "")
    assert orch.llm.calls >= 2  # native decisions were used


def test_decide_downgrades_on_tools_unsupported(tmp_path):
    """If the endpoint rejects tools, _decide downgrades once and uses the JSON
    protocol (complete_json) for the rest of the run."""
    orch = _orch(tmp_path)

    class _Downgrading(LLMClient):
        def supports_tool_calling(self):
            return True
        def complete_with_tools(self, messages, tools):
            raise ToolsUnsupported("nope")
        def complete_json(self, messages, *, schema_hint=""):
            return {"action": "finish", "answer": "via json"}

    orch.llm = _Downgrading()
    loop = AskAgentLoop(orch)
    decision = loop._decide([LLMMessage("user", "q")])
    assert decision == {"action": "finish", "answer": "via json"}
    assert loop._tools_downgraded is True
