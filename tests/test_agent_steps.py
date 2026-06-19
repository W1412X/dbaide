from dbaide.step_budget import (
    DEFAULT_AGENT_MAX_STEPS,
    MAX_AGENT_MAX_STEPS,
    MIN_AGENT_MAX_STEPS,
    MIN_SUBAGENT_MAX_STEPS,
    MAX_SUBAGENT_MAX_STEPS,
    clamp_agent_max_steps,
    child_step_budget,
)
from dbaide.agent.progress_events import progress_event, subagent_event
from dbaide.agent.trace_model import (
    build_trace_model_from_events,
    count_timeline_steps,
    localized_summary_line,
    step_count_from_events,
)


def test_clamp_agent_max_steps():
    assert clamp_agent_max_steps(None) == DEFAULT_AGENT_MAX_STEPS
    assert clamp_agent_max_steps(0) == MIN_AGENT_MAX_STEPS
    assert clamp_agent_max_steps(999) == MAX_AGENT_MAX_STEPS


def test_child_step_budget_respects_parent_and_caps():
    assert child_step_budget(None, 64) == 24
    assert child_step_budget(10, 64) == 10
    assert child_step_budget(999, 64) == MAX_SUBAGENT_MAX_STEPS
    assert child_step_budget(10, 8) == 8
    assert child_step_budget(3, 64) == MIN_SUBAGENT_MAX_STEPS


def test_step_count_from_events_matches_timeline():
    events = [
        progress_event(stage="loop", title="started", status="running", kind="agent"),
        progress_event(stage="discover_schema", title="Calling", status="running", kind="tool", step=1),
        subagent_event(agent="schema_link", title="db1", parent="discover_schema", node_id="schema:1", status="completed"),
        progress_event(stage="discover_schema", title="done", status="completed", kind="tool", step=1),
    ]
    model = build_trace_model_from_events(events, live=True)
    assert step_count_from_events(events, live=True) == count_timeline_steps(model)
    finalized = build_trace_model_from_events(events, live=False)
    assert step_count_from_events(events, live=False) == count_timeline_steps(finalized)


def test_running_summary_uses_timeline_step_count():
    events = [
        progress_event(stage="loop", title="started", status="running", kind="agent"),
        progress_event(stage="discover_schema", title="Calling", status="running", kind="tool", step=1),
        subagent_event(agent="schema_link", title="db1", parent="discover_schema", node_id="schema:1", status="running"),
    ]
    model = build_trace_model_from_events(events, live=True)
    line = localized_summary_line(model)
    n = count_timeline_steps(model)
    assert str(n) in line or f"{n} steps" in line.lower() or f"{n} 步" in line
