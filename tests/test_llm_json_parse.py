"""Regression: the agent must not abort when a decision's JSON string contains a
literal control character (a raw newline in a multi-line markdown answer). Strict
json.loads rejected it with "Invalid control character", crashing the whole run at
the final step even though the answer content was fine."""

import pytest

from dbaide.agent.answer_stream import JsonFieldStreamer
from dbaide.llm import OpenAICompatibleClient
from dbaide.models import ModelConfig


def _client():
    return OpenAICompatibleClient(ModelConfig(
        provider="openai_compatible", base_url="http://x/v1", api_key="k", model="m"))


def test_parse_tolerates_literal_newline_in_string():
    # A finish decision whose answer markdown has a REAL newline (not \\n) — exactly
    # what triggered "Invalid control character at line 1 column ...".
    raw = '{"action":"finish","answer":"Line one\nLine two\twith tab"}'
    obj = _client()._parse_json_object(raw)
    assert obj["action"] == "finish"
    assert obj["answer"] == "Line one\nLine two\twith tab"


def test_parse_tolerates_fenced_and_control_chars():
    raw = '```json\n{"action":"finish","answer":"a\nb"}\n```'
    obj = _client()._parse_json_object(raw)
    assert obj["answer"] == "a\nb"


def test_parse_still_rejects_truly_invalid_json():
    with pytest.raises(ValueError):
        _client()._parse_json_object("not json at all {")


def test_openai_client_rejects_non_http_base_url():
    with pytest.raises(ValueError, match="http"):
        OpenAICompatibleClient(ModelConfig(
            provider="openai_compatible", base_url="file:///tmp/model", api_key="k", model="m"))


def test_parse_repairs_unescaped_quote_in_answer():
    raw = '{"action":"finish","answer":"The result is "good" overall"}'
    obj = _client()._parse_json_object(raw)
    assert obj["action"] == "finish"
    assert "good" in obj["answer"]


def test_parse_repairs_unescaped_quotes_in_markdown_table():
    raw = '{"action":"finish","answer":"| col |\n| "value" | "other" |"}'
    obj = _client()._parse_json_object(raw)
    assert obj["action"] == "finish"
    assert "value" in obj["answer"]


def test_parse_handles_fenced_block_with_content_before_and_after():
    raw = 'Here is the JSON:\n```json\n{"action":"call_tool","tool":"x","args":{}}\n```\nDone.'
    obj = _client()._parse_json_object(raw)
    assert obj["action"] == "call_tool"


def test_streamer_handles_literal_newline_in_answer():
    out = []
    s = JsonFieldStreamer(out.append, field="answer")
    s.feed('{"action":"finish","answer":"first\n')
    s.feed('second"}')
    assert "".join(out) == "first\nsecond"


def test_streamer_flush_final_emits_tail_after_partial_decode_gap():
    """When partial JSON decode skips a slice, flush_final must emit the remainder."""
    out = []
    s = JsonFieldStreamer(out.append, field="answer")
    s.feed('{"action":"finish","answer":"hello')
    assert "".join(out) == "hello"
    s.flush_final("hello world")
    assert "".join(out) == "hello world"
