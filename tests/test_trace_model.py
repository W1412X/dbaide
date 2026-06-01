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


def test_framing_events_are_not_steps():
    m = TraceModel()
    _feed(m, [
        {"stage": "workflow_started", "title": "start", "status": "completed", "kind": "agent"},
        {"stage": "planning", "title": "plan", "status": "completed", "kind": "agent"},
        progress_event(stage="execute_sql", title="x", status="completed", kind="tool", step=1),
        {"stage": "workflow_completed", "title": "done", "status": "completed", "kind": "agent"},
    ])
    assert len(m.steps) == 1
    assert m.overall == "done"


def test_finalize_marks_running_steps_completed():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="execute_sql", title="x", status="running", kind="tool", step=1),
    ])
    assert m.steps[0].status == "running"
    m.finalize()
    assert m.steps[0].status == "completed"
    assert m.overall == "done"


def test_finalize_failed_keeps_failure():
    m = TraceModel()
    _feed(m, [progress_event(stage="execute_sql", title="x", status="running", kind="tool", step=1)])
    m.finalize(failed=True)
    assert m.overall == "failed"


def test_parallel_subtasks_are_siblings_via_node_id():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="discover_schema", title="discover", status="running", kind="tool", step=1),
        subagent_event(agent="schema_link", title="db1: kept 3", parent="discover_schema", node_id="schema:db1", status="completed"),
        subagent_event(agent="schema_link", title="db2: kept 1", parent="discover_schema", node_id="schema:db2", status="completed"),
    ])
    step = m.steps[0]
    assert len(step.children) == 2  # two parallel db scans, same level
    assert {c.id for c in step.children} == {"schema:db1", "schema:db2"}


def test_node_id_merges_running_then_done():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="validate_joins", title="validate", status="running", kind="tool", step=1),
        subagent_event(agent="join_validate", title="Sample check a→b", parent="validate_joins", node_id="jv:a->b", status="info"),
        subagent_event(agent="join_validate", title="a→b · one_to_many · 80%", parent="validate_joins", node_id="jv:a->b", status="completed"),
    ])
    step = m.steps[0]
    assert len(step.children) == 1            # one relation = one node, updated
    node = step.children[0]
    assert node.status == "completed"
    assert "80%" in node.title


def test_arbitrary_depth_via_parent_id():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="discover_schema", title="d", status="running", kind="tool", step=1),
        subagent_event(agent="schema_link", title="db1", parent="discover_schema", node_id="schema:db1"),
        subagent_event(agent="schema_link", title="table users", node_id="schema:db1:users", parent_id="schema:db1"),
    ])
    db = m.find("schema:db1")
    assert db is not None
    assert len(db.children) == 1
    assert db.children[0].id == "schema:db1:users"


def test_find_and_descendant_agents():
    m = TraceModel()
    _feed(m, [
        progress_event(stage="execute_sql", title="x", status="running", kind="tool", step=1),
        subagent_event(agent="risk", title="ok", parent="execute_sql", node_id="risk:1", status="completed"),
        subagent_event(agent="explain", title="10 rows", parent="execute_sql", node_id="explain:1", status="completed"),
    ])
    assert m.find("step:1") is not None
    assert set(m.active_agents) == {"Risk gate", "Cost estimate"}


def test_raw_event_preserved_for_detail_view():
    m = TraceModel()
    m.ingest(progress_event(stage="execute_sql", title="x", detail="SELECT 1", status="completed",
                            kind="tool", step=1, duration_ms=5), now=1.0)
    node = m.steps[0]
    assert node.raw.get("detail") == "SELECT 1"
    assert node.raw.get("stage") == "execute_sql"


def test_execute_step_carries_sql_for_audit():
    """The execute/explain/generate step node must carry the exact SQL so clicking
    it in the trace surfaces what the system ran (full SQL auditability)."""
    from dbaide.agent.loop import _executed_sql, _SQL_TOOLS

    class _Orch:
        _loop_sql = "SELECT 1 FROM t"

    class _Res:
        data = {"sql": "SELECT * FROM users LIMIT 100", "row_count": 3}

    # execute pulls the SQL from the tool result
    assert _executed_sql("execute_sql", _Orch(), _Res()) == "SELECT * FROM users LIMIT 100"
    # generate/validate fall back to the loop's current SQL
    class _Empty:
        data = {}
    assert _executed_sql("generate_sql", _Orch(), _Empty()) == "SELECT 1 FROM t"
    # non-SQL tools carry nothing
    assert _executed_sql("describe_table", _Orch(), _Res()) == ""
    assert "execute_sql" in _SQL_TOOLS and "explain_sql" in _SQL_TOOLS
