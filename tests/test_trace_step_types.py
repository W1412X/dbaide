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


def test_trace_model_supports_arbitrary_depth():
    """A sub-step can nest under another sub-step (not just under a tool), so the
    tree has no 2-level cap."""
    model = TraceModel()
    model.ingest({"stage": "discover_schema", "title": "Calling", "status": "running",
                  "kind": "tool", "step": 1})
    model.ingest({"stage": "schema_link", "title": "scan", "status": "running",
                  "kind": "substep", "node_id": "sl", "parent": "discover_schema"})
    model.ingest({"stage": "db", "title": "shop", "status": "running",
                  "kind": "substep", "node_id": "sl:shop", "parent_id": "sl"})
    model.ingest({"stage": "tbl", "title": "orders", "status": "completed",
                  "kind": "substep", "node_id": "sl:shop:orders", "parent_id": "sl:shop"})
    model.finalize()

    def depth(nid):
        d, n = 0, model.find(nid)
        while n and n.parent_id and n.parent_id != "__root__":
            d += 1
            n = model.find(n.parent_id)
        return d

    assert depth("sl") == 1
    assert depth("sl:shop") == 2
    assert depth("sl:shop:orders") == 3  # nests three levels under the tool step
    # nesting by `parent` stage name also resolves to a non-tool node
    model.ingest({"stage": "x", "title": "leaf", "status": "completed",
                  "kind": "substep", "node_id": "leaf1", "parent": "tbl"})
    assert model.find("leaf1").parent_id == "sl:shop:orders"


def test_trace_model_build_phase_nodes_are_typed_phase():
    model = TraceModel()
    model.ingest({"stage": "build_assets", "title": "Building assets · shop",
                  "status": "running", "kind": "build", "node_id": "build:root"})
    model.finalize()
    assert model.steps[0].node_type == "phase"
