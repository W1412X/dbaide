from dbaide.agent.progress_events import (
    brief_tool_summary,
    conversation_trace_step,
    normalize_trace_key,
    progress_event,
    progress_label,
    trace_dedupe_keys,
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


def test_trace_dedupe_keys_links_live_and_persisted():
    live = progress_event(stage="discover_schema", title="Calling discover_schema", status="running")
    persisted = {
        "stage": "discover_schema",
        "title": progress_label(live),
        "summary": "discover_schema: Calling discover_schema",
    }
    assert trace_dedupe_keys(live) & trace_dedupe_keys(persisted)


def test_conversation_trace_step_substep():
    step = conversation_trace_step(
        progress_event(stage="discover_schema", title="Screening 3 database(s)", status="info", kind="substep")
    )
    assert step == ("Screening 3 database(s)", "info", "")


def test_conversation_trace_step_skips_planning():
    assert conversation_trace_step({"stage": "planning", "title": "Planning SQL"}) is None


def test_conversation_trace_step_sql_generated():
    step = conversation_trace_step({"stage": "sql_generated", "title": "SQL generated", "output_preview": "SELECT 1"})
    assert step is not None
    assert step[0] == "SQL generated"
    assert "SELECT 1" in step[2]
