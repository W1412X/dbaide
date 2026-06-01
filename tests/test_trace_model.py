"""Tests for the TraceModel event aggregator."""

from __future__ import annotations

from dbaide.agent.progress_events import progress_event, subagent_event
from dbaide.agent.trace_model import TraceModel


def _feed(model, events):
    t = 1000.0
    for ev in events:
        model.ingest(ev, now=t)
        t += 1.0


def test_tool_running_then_completed_collapses_to_one_step():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="loop", title="Agent loop started", status="running", kind="agent"),
        progress_event(stage="execute_sql", title="Calling execute_sql", status="running", kind="tool", step=1),
        progress_event(stage="execute_sql", title="execute_sql done", status="completed", kind="tool", step=1, duration_ms=42),
    ])
    assert len(m.steps) == 1
    step = m.steps[0]
    assert step.phase == "Running query"
    assert step.status == "completed"
    assert step.duration_ms == 42
    assert step.step == 1


def test_same_stage_distinct_steps_do_not_collide():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="describe_table", title="Calling describe_table", status="running", kind="tool", step=1),
        progress_event(stage="describe_table", title="describe_table done", status="completed", kind="tool", step=1),
        progress_event(stage="describe_table", title="Calling describe_table", status="running", kind="tool", step=2),
        progress_event(stage="describe_table", title="describe_table done", status="completed", kind="tool", step=2),
    ])
    assert len(m.steps) == 2
    assert all(s.status == "completed" for s in m.steps)


def test_thought_attaches_to_next_step():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="decision", title="I should read the orders table", status="completed", kind="decision"),
        progress_event(stage="describe_table", title="Calling describe_table", status="running", kind="tool", step=1),
    ])
    assert m.steps[0].thought == "I should read the orders table"


def test_substeps_nest_under_parent_and_count_agents():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="execute_sql", title="Calling execute_sql", status="running", kind="tool", step=1),
        subagent_event(agent="risk", title="Risk: auto_execute", parent="execute_sql", status="completed"),
        subagent_event(agent="explain", title="EXPLAIN ~10 rows", parent="execute_sql", status="completed"),
    ])
    step = m.steps[0]
    assert len(step.substeps) == 2
    assert "Risk gate" in step.agents
    assert "Cost estimate" in step.agents


def test_current_phase_and_step_track_latest_running():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="discover_schema", title="x", status="running", kind="tool", step=1),
        progress_event(stage="discover_schema", title="x done", status="completed", kind="tool", step=1),
        progress_event(stage="generate_sql", title="y", status="running", kind="tool", step=2),
    ])
    assert m.current_step == 2
    assert m.current_phase == "Writing SQL"


def test_overall_done_on_loop_finish():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="loop", title="started", status="running", kind="agent"),
        progress_event(stage="execute_sql", title="x", status="completed", kind="tool", step=1),
        progress_event(stage="loop", title="finished", status="completed", kind="agent"),
    ])
    assert m.overall == "done"
    assert "Done" in m.summary_line(now=2000.0)


def test_elapsed_uses_event_span_when_done():
    m = TraceModel()
    m.ingest(progress_event(stage="loop", title="s", status="running", kind="agent"), now=1000.0)
    m.ingest(progress_event(stage="execute_sql", title="x", status="completed", kind="tool", step=1), now=1003.0)
    m.ingest(progress_event(stage="loop", title="f", status="completed", kind="agent"), now=1005.0)
    assert m.elapsed_ms() == 5000.0  # frozen at last event once done


def test_build_assets_collapses_to_single_step():
    m = TraceModel()
    _feed(m, [
        {"stage": "build_assets", "title": "listing tables", "status": "running", "kind": "info"},
        {"stage": "build_assets", "title": "describing t", "status": "running", "kind": "info"},
    ])
    assert len(m.steps) == 1
    assert m.steps[0].phase == "Building assets"
    assert m.steps[0].title == "describing t"


def test_summary_line_lists_active_agents():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="get_relations", title="rel", status="running", kind="tool", step=1),
        subagent_event(agent="join_infer", title="inferring", parent="get_relations"),
        subagent_event(agent="join_validate", title="validating", parent="get_relations"),
    ])
    line = m.summary_line(now=1010.0)
    assert "Mapping relations" in line
    assert "2 agents" in line
