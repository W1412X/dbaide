"""Trace steps are classified into canonical types so the UI can render each
kind of work (SQL, phase, tool, thought) consistently."""

from dbaide.agent.progress_events import step_type
from dbaide.agent.trace_model import TraceModel


def test_step_type_detects_sql_by_stage_and_field():
    assert step_type({"stage": "execute_sql", "kind": "tool"}, is_tool=True) == "sql"
    assert step_type({"stage": "explain_sql", "kind": "tool"}, is_tool=True) == "sql"
    # A non-SQL stage still becomes 'sql' once a sql payload is present.
    assert step_type({"stage": "whatever", "sql": "SELECT 1"}) == "sql"


def test_step_type_other_categories():
    assert step_type({"stage": "decision", "kind": "decision"}) == "decision"
    assert step_type({"kind": "substep", "agent": "risk"}) == "substep"
    assert step_type({"node_id": "build:db:main", "kind": "build"}) == "phase"
    assert step_type({"stage": "describe_table", "kind": "tool"}, is_tool=True) == "tool"
    assert step_type({"stage": "x"}) == "info"


def test_trace_model_assigns_sql_type_and_upgrades_on_done():
    model = TraceModel()
    # "Calling" frame: execute_sql stage already classifies as sql.
    model.ingest({"stage": "execute_sql", "title": "Calling execute_sql",
                  "status": "running", "kind": "tool", "step": 1})
    # "done" frame carries the actual SQL + facts.
    model.ingest({"stage": "execute_sql", "title": "execute_sql done", "status": "completed",
                  "kind": "tool", "step": 1, "sql": "SELECT count(*) FROM orders",
                  "row_count": 3, "database": "main"})
    model.finalize()
    step = model.steps[0]
    assert step.node_type == "sql"
    assert step.raw.get("sql") == "SELECT count(*) FROM orders"
    assert step.raw.get("row_count") == 3


def test_trace_model_build_phase_nodes_are_typed_phase():
    model = TraceModel()
    model.ingest({"stage": "build_assets", "title": "Building assets · shop",
                  "status": "running", "kind": "build", "node_id": "build:root"})
    model.finalize()
    assert model.steps[0].node_type == "phase"
