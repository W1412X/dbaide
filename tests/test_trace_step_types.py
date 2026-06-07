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


def test_trace_model_keeps_decide_as_clickable_llm_step():
    from dbaide.agent.trace_model import render_trace_text

    model = TraceModel()
    model.ingest({
        "stage": "decide",
        "title": "Need schema evidence",
        "status": "completed",
        "kind": "llm",
        "node_id": "decision:1",
        "decision": {"action": "call_tool", "tool": "retrieve_schema_context"},
        "llm_calls": [{
            "stage": "decide",
            "method": "complete_json",
            "messages": [{"role": "user", "content": "输入问题：5月份妥投多少件"}],
            "response": '{"action":"call_tool"}',
        }],
    })
    model.finalize()

    assert model.steps[0].node_type == "llm"
    assert model.steps[0].stage == "decide"
    text = render_trace_text(model)
    assert "Thinking" in text or "思考" in text
    assert "retrieve_schema_context" in text
    assert "5月份妥投多少件" in text


def test_trace_text_follows_ui_language():
    from dbaide.agent.trace_model import render_trace_text
    from dbaide.i18n import set_language

    set_language("zh")
    try:
        model = TraceModel()
        model.ingest({
            "stage": "decide",
            "title": "Need schema evidence",
            "status": "completed",
            "kind": "llm",
            "node_id": "decision:1",
            "decision": {"action": "call_tool", "tool": "retrieve_schema_context"},
        })
        model.ingest({
            "stage": "retrieve_schema_context",
            "title": "retrieve_schema_context done",
            "status": "completed",
            "kind": "tool",
            "step": 1,
            "detail": "orders",
        })
        model.finalize()
        text = render_trace_text(model)
        assert "思考中" in text
        assert "工具完成：retrieve_schema_context" in text
    finally:
        set_language("en")


def test_trace_model_keeps_main_loop_hierarchy_and_structured_io():
    from dbaide.agent.trace_model import render_trace_text

    model = TraceModel()
    model.ingest({
        "stage": "loop",
        "title": "Agent loop",
        "status": "running",
        "kind": "phase",
        "node_id": "loop",
    })
    model.ingest({
        "stage": "decide",
        "title": "Need schema evidence",
        "status": "completed",
        "kind": "llm",
        "node_id": "decision:1",
        "parent_id": "loop",
        "decision": {"action": "call_tool", "tool": "retrieve_schema_context"},
    })
    model.ingest({
        "stage": "retrieve_schema_context",
        "title": "retrieve_schema_context done",
        "status": "completed",
        "kind": "tool",
        "step": 1,
        "parent_id": "loop",
        "args": {"request": "5月份妥投多少件"},
        "result_data": {"candidates": [{"table": "orders"}]},
    })
    model.ingest({
        "stage": "schema_link",
        "title": "Recall schema candidates",
        "status": "completed",
        "kind": "substep",
        "agent": "schema_link",
        "node_id": "step:1/schema",
        "parent_id": "step:1",
    })
    model.finalize()

    assert model.find("decision:1").parent_id == "loop"
    assert model.find("step:1").parent_id == "loop"
    assert model.find("step:1/schema").parent_id == "step:1"
    text = render_trace_text(model)
    assert "5月份妥投多少件" in text
    assert "orders" in text


def test_llm_calls_expand_into_clickable_child_steps():
    from dbaide.agent.trace_model import render_trace_text

    model = TraceModel()
    model.ingest({
        "stage": "retrieve_schema_context",
        "title": "retrieve_schema_context done",
        "status": "completed",
        "kind": "tool",
        "step": 1,
        "args": {"request": "5月份妥投多少件"},
        "llm_calls": [{
            "stage": "schema_filter",
            "method": "complete_json",
            "messages": [
                {"role": "system", "content": "select relevant tables"},
                {"role": "user", "content": "orders, shipments"},
            ],
            "response": '{"relevant_indices":[0,1]}',
            "ms": 9.5,
        }],
    })
    model.finalize()

    parent = model.find("step:1")
    child = model.find("step:1/llm:1")
    assert parent is not None
    assert child is not None and child.parent_id == parent.id
    assert child.node_type == "llm"
    assert child.raw["llm_call"]["messages"][0]["content"] == "select relevant tables"
    text = render_trace_text(model)
    assert "schema_filter" in text
    assert "select relevant tables" in text
    assert "relevant_indices" in text
