from dbaide.agent.llm_trace import RecordingLLMClient, llm_stage
from dbaide.agent.trace_model import render_events_text
from dbaide.llm import LLMClient, LLMMessage

class _Echo(LLMClient):
    def complete_json(self, messages, *, schema_hint=""): return {"ok": True}
    def complete_text(self, messages): return "hi"

def test_recorder_captures_with_stage():
    rec = RecordingLLMClient(_Echo())
    start = rec.snapshot_len()
    with llm_stage("resolve_schema"):
        rec.complete_json([LLMMessage("system", "you are a schema linker"),
                           LLMMessage("user", "Q + candidates")])
    calls = rec.since(start)
    assert len(calls) == 1
    assert calls[0]["stage"] == "resolve_schema"
    assert calls[0]["messages"][0]["content"] == "you are a schema linker"
    assert '"ok": true' in calls[0]["response"]

def test_render_includes_llm_calls():
    events = [{
        "stage": "generate_sql", "title": "generate_sql done", "kind": "tool",
        "status": "completed", "step": 1,
        "llm_calls": [{"stage": "generate_sql", "method": "complete_json", "ms": 12.0,
                       "messages": [{"role": "system", "content": "generate safe read-only SQL"},
                                    {"role": "user", "content": "Table: orders"}],
                       "response": '{"sql": "SELECT 1"}'}],
    }]
    txt = render_events_text(events)
    assert "llm calls: 1" in txt
    assert "generate safe read-only SQL" in txt
    assert "Table: orders" in txt
    assert "SELECT 1" in txt
