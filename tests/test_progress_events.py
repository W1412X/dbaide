from dbaide.agent.progress_events import (
    brief_tool_summary,
    conversation_trace_step,
    normalize_trace_key,
    progress_event,
    progress_label,
    subagent_event,
)


def test_progress_label_from_dict():
    label = progress_label(
        progress_event(stage="discover_schema", title="Screening 3 database(s)", detail="2 hits")
    )
    assert "discover_schema" in label
    assert "Screening" in label


def test_brief_tool_summary_discover():
    class Result:
        ok = True
        data = {"count": 5, "hits": [{}] * 5}

    assert "5" in brief_tool_summary("discover_schema", Result())


def test_exploratory_sql_progress_is_tool_and_query():
    class Result:
        ok = True
        data = {"row_count": 3}

    assert brief_tool_summary("execute_readonly_sql", Result()) == "3 rows"


def test_brief_tool_summary_render_chart():
    class Result:
        ok = True
        data = {"title": "Revenue trend", "chart_type": "line", "row_count": 12}

    assert brief_tool_summary("render_chart", Result()) == "Revenue trend (12 pts)"


def test_conversation_trace_step_from_progress():
    step = conversation_trace_step(
        progress_event(stage="execute_readonly_sql", title="execute_readonly_sql done", status="completed", kind="tool")
    )
    assert step is not None
    assert step[1] == "tool"
    assert "execute_readonly_sql" in step[0]


def test_progress_label_includes_detail():
    label = progress_label(
        progress_event(stage="describe_table", title="Calling describe_table", detail="orders")
    )
    assert "describe_table" in label
    assert "orders" in label


def test_normalize_trace_key_strips_detail_suffix():
    assert normalize_trace_key("discover_schema: Calling discover_schema — args") == normalize_trace_key(
        "discover_schema: Calling discover_schema"
    )


def test_conversation_trace_step_substep():
    step = conversation_trace_step(
        progress_event(stage="discover_schema", title="Screening 3 database(s)", status="info", kind="substep")
    )
    assert step == ("Screening 3 database(s)", "info", "")


def test_subagent_event_nested():
    ev = subagent_event(agent="schema_link", title="LLM filter table · batch 1", parent="discover_schema", detail="18 objects")
    assert ev["parent"] == "discover_schema"
    assert ev["agent"] == "schema_link"
    assert ev["kind"] == "substep"


def test_agent_label_chart_agent():
    from dbaide.agent.progress_events import agent_label

    assert agent_label("chart_agent") == "Chart planning"


def test_conversation_trace_step_subagent_agent_prefix():
    step = conversation_trace_step(
        subagent_event(agent="schema_link", title="Kept 2 database(s)", parent="discover_schema"),
    )
    assert step is not None
    assert step[0].startswith("schema_link:")
    assert "Kept 2" in step[0]


def test_conversation_trace_step_skips_planning():
    assert conversation_trace_step({"stage": "planning", "title": "Planning SQL"}) is None


def test_conversation_trace_step_sql_generated():
    step = conversation_trace_step({"stage": "sql_generated", "title": "SQL generated", "output_preview": "SELECT 1"})
    assert step is not None
    assert step[0] == "SQL generated"
    assert "SELECT 1" in step[2]
