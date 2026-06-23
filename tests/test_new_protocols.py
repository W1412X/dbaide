"""Anthropic Messages API + OpenAI Responses API clients (raw-HTTP LLMClient subclasses)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from dbaide.llm import (
    AnthropicClient,
    NullLLMClient,
    OpenAIResponsesClient,
    ToolsUnsupported,
    build_llm_client,
    LLMMessage,
)
from dbaide.models import ModelConfig


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def _capture(monkeypatch, payload, *, status=None):
    captured: dict = {}

    def fake(req, *a, **k):
        captured["url"] = req.full_url
        captured["headers"] = {kk.lower(): vv for kk, vv in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        if status is not None:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(b'{"error":"nope"}'))
        return _Resp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake)
    return captured


_TOOL = {"type": "function", "function": {
    "name": "describe_table", "description": "d",
    "input_schema": None, "parameters": {"type": "object", "properties": {"table": {"type": "string"}}},
}}


def _anthropic():
    return AnthropicClient(ModelConfig(provider="anthropic", api_key="k", model="claude-opus-4-8"))


def _responses():
    return OpenAIResponsesClient(ModelConfig(provider="openai_responses", api_key="k", model="gpt-x"))


# ── dispatch ──────────────────────────────────────────────────────────────────

def test_build_llm_client_dispatches_by_provider():
    assert isinstance(build_llm_client(ModelConfig(provider="anthropic", api_key="k", model="m")), AnthropicClient)
    assert isinstance(build_llm_client(ModelConfig(provider="openai_responses", api_key="k", model="m")), OpenAIResponsesClient)
    assert isinstance(build_llm_client(ModelConfig(provider="none")), NullLLMClient)


# ── Anthropic ───────────────────────────────────────────────────────────────

def test_anthropic_hoists_system_and_maps_usage(monkeypatch):
    cap = _capture(monkeypatch, {
        "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 3, "output_tokens": 5},
    })
    client = _anthropic()
    text = client.complete_text([LLMMessage("system", "sys"), LLMMessage("user", "q")])
    assert text == "hi"
    assert cap["url"].endswith("/v1/messages")
    assert cap["headers"].get("x-api-key") == "k"
    assert cap["headers"].get("anthropic-version") == "2023-06-01"
    assert cap["body"]["system"] == "sys"
    assert cap["body"]["messages"] == [{"role": "user", "content": "q"}]
    assert "temperature" not in cap["body"]            # removed on Opus 4.7/4.8/Fable
    assert client.last_usage == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}


def test_anthropic_complete_with_tools_parses_tool_use(monkeypatch):
    cap = _capture(monkeypatch, {
        "content": [
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "name": "describe_table", "input": {"table": "orders"}},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })
    result = _anthropic().complete_with_tools([LLMMessage("user", "q")], tools=[_TOOL])
    assert result["content"] == "let me check"
    assert result["tool_calls"] == [{"name": "describe_table", "arguments": {"table": "orders"}}]
    assert cap["body"]["tool_choice"] == {"type": "auto"}
    assert cap["body"]["tools"] == [{
        "name": "describe_table", "description": "d",
        "input_schema": {"type": "object", "properties": {"table": {"type": "string"}}},
    }]


def test_anthropic_tools_rejected_raises_unsupported(monkeypatch):
    _capture(monkeypatch, {}, status=400)
    with pytest.raises(ToolsUnsupported):
        _anthropic().complete_with_tools([LLMMessage("user", "q")], tools=[_TOOL])


def test_anthropic_base_url_default_and_v1_strip():
    assert _anthropic().base_url == "https://api.anthropic.com"
    c = AnthropicClient(ModelConfig(provider="anthropic", api_key="k", model="m",
                                    base_url="https://proxy.example.com/v1"))
    assert c.base_url == "https://proxy.example.com"


def test_anthropic_complete_json(monkeypatch):
    _capture(monkeypatch, {"content": [{"type": "text", "text": '```json\n{"action": "finish"}\n```'}]})
    assert _anthropic().complete_json([LLMMessage("user", "q")], schema_hint="emit json") == {"action": "finish"}


# ── OpenAI Responses ──────────────────────────────────────────────────────────

def test_responses_reads_output_and_instructions(monkeypatch):
    cap = _capture(monkeypatch, {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "ans"}]}],
        "usage": {"input_tokens": 2, "output_tokens": 4},
    })
    client = _responses()
    assert client.complete_text([LLMMessage("system", "sys"), LLMMessage("user", "q")]) == "ans"
    assert cap["url"].endswith("/responses")
    assert cap["headers"].get("authorization") == "Bearer k"
    assert cap["body"]["instructions"] == "sys"
    assert cap["body"]["input"] == [{"role": "user", "content": "q"}]
    assert client.last_usage == {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6}


def test_responses_output_text_convenience(monkeypatch):
    _capture(monkeypatch, {"output_text": "quick", "usage": {"input_tokens": 1, "output_tokens": 1}})
    assert _responses().complete_text([LLMMessage("user", "q")]) == "quick"


def test_responses_complete_with_tools_parses_function_call(monkeypatch):
    cap = _capture(monkeypatch, {
        "output": [{"type": "function_call", "name": "list_tables", "arguments": '{"db":"x"}', "call_id": "c1"}],
    })
    result = _responses().complete_with_tools([LLMMessage("user", "q")], tools=[_TOOL])
    assert result["tool_calls"] == [{"name": "list_tables", "arguments": {"db": "x"}}]
    assert cap["body"]["tool_choice"] == "auto"
    assert cap["body"]["tools"][0]["type"] == "function"
    assert cap["body"]["tools"][0]["name"] == "describe_table"


def test_responses_base_url_default():
    assert _responses().base_url == "https://api.openai.com/v1"
