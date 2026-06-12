"""Structured progress payloads for GUI trace and status bar."""

from __future__ import annotations

from typing import Any

from dbaide.core.events import TraceEvent

TOOL_TRACE_STAGES = frozenset({
    "discover_schema",
    "retrieve_schema_context",
    "describe_table",
    "inspect_metadata",
    "retrieve_join_context",
    "generate_sql",
    "validate_sql",
    "execute_sql",
    "execute_readonly_sql",
    "validate_joins",
    "list_joins",
    "add_join",
    "update_join",
    "delete_join",
    "ask_user",
    "profile_table",
    "column_stats",
    "explain_sql",
    "list_databases",
    "list_tables",
})

# Human-readable phase for each tool stage — what the agent is *doing* right now.
PHASE_LABELS: dict[str, str] = {
    "discover_schema": "Exploring schema",
    "retrieve_schema_context": "Reading schema evidence",
    "list_databases": "Exploring schema",
    "list_tables": "Exploring schema",
    "describe_table": "Reading tables",
    "inspect_metadata": "Inspecting metadata",
    "retrieve_join_context": "Mapping relations",
    "validate_joins": "Mapping relations",
    "list_joins": "Mapping relations",
    "add_join": "Mapping relations",
    "update_join": "Mapping relations",
    "delete_join": "Mapping relations",
    "generate_sql": "Writing SQL",
    "validate_sql": "Validating SQL",
    "explain_sql": "Checking query cost",
    "execute_sql": "Running query",
    "execute_readonly_sql": "Running query",
    "profile_table": "Profiling data",
    "column_stats": "Profiling data",
    "ask_user": "Waiting for you",
    "build_assets": "Building assets",
    "environment_check": "Checking environment",
    "agent_request": "Starting agent",
}

# Friendly names for the named sub-agents that report nested progress.
AGENT_LABELS: dict[str, str] = {
    "schema_link": "Schema discovery",
    "sql_writer": "SQL writer",
    "join_infer": "Join inference",
    "join_validate": "Join validation",
    "join_catalog": "Join catalog",
    "risk": "Risk gate",
    "explain": "Cost estimate",
    "sql": "SQL",
}


# ── Step types ───────────────────────────────────────────────────────────────
# Every trace node is classified into one of these canonical types so the UI can
# render each kind of work consistently (a SQL execution always shows its query,
# a build phase groups its children, a thought reads as reasoning, …).
STEP_TYPES = ("phase", "tool", "sql", "llm", "decision", "io", "substep", "info")

# Stages that *are* a SQL execution regardless of how they were emitted.
SQL_STEP_STAGES = frozenset({"execute_sql", "execute_readonly_sql", "explain_sql"})

# Short, human label per type — shown as a chip on the step so types are legible.
STEP_TYPE_LABELS: dict[str, str] = {
    "phase": "Phase",
    "tool": "Tool",
    "sql": "SQL",
    "llm": "Model",
    "decision": "Think",
    "io": "I/O",
    "substep": "",
    "info": "",
}


def step_type(event: dict[str, Any], *, is_tool: bool = False) -> str:
    """Classify a progress/trace event into one canonical :data:`STEP_TYPES` value.

    SQL is detected first (a step that ran a query is a ``sql`` step no matter what
    ``kind`` it carried), then explicit kinds, then structural fallbacks.
    """
    kind = str(event.get("kind") or "").strip()
    stage = str(event.get("stage") or "").strip()
    if event.get("sql") or stage in SQL_STEP_STAGES or kind == "sql":
        return "sql"
    if kind == "decision" or stage == "decision":
        return "decision"
    if kind == "llm":
        return "llm"
    if kind == "io":
        return "io"
    if kind == "substep":
        return "substep"
    node_id = str(event.get("node_id") or "")
    if kind == "phase" or stage == "build_assets" or _node_namespace(node_id) == "build":
        return "phase"
    if is_tool:
        return "tool"
    return "info"


def child_node(parent_id: str, label: str) -> str:
    """A stable hierarchical node id for a child activity, so a sub-agent/sub-tool
    nests under its caller in the trace (true call tree). Empty parent → top level."""
    slug = normalize_trace_key(label).replace(" ", "_")[:40] or "node"
    return f"{parent_id}/{slug}" if parent_id else slug


def phase_for(stage: str) -> str:
    """Map a tool/stage name to the human phase it belongs to."""
    return PHASE_LABELS.get(str(stage or "").strip(), "")


def agent_label(agent: str) -> str:
    name = str(agent or "").strip()
    return AGENT_LABELS.get(name, name.replace("_", " ").title() if name else "")


def progress_event(
    *,
    stage: str,
    title: str,
    status: str = "running",
    kind: str = "agent",
    detail: str = "",
    duration_ms: float = 0.0,
    parent: str = "",
    agent: str = "",
    step: int = 0,
    phase: str = "",
    node_id: str = "",
    parent_id: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stage": stage,
        "title": title,
        "status": status,
        "kind": kind,
    }
    if detail:
        payload["detail"] = detail
    if duration_ms > 0:
        payload["duration_ms"] = duration_ms
    if parent:
        payload["parent"] = parent
    if agent:
        payload["agent"] = agent
    if step > 0:
        payload["step"] = step
    if node_id:
        payload["node_id"] = node_id
    if parent_id:
        payload["parent_id"] = parent_id
    resolved_phase = phase or phase_for(stage)
    if resolved_phase:
        payload["phase"] = resolved_phase
    return payload


def subagent_event(
    *,
    agent: str,
    title: str,
    parent: str = "",
    detail: str = "",
    status: str = "info",
    stage: str = "",
    node_id: str = "",
    parent_id: str = "",
) -> dict[str, Any]:
    """Progress line for a nested sub-agent (schema_link, sql_writer, risk, …).

    ``node_id`` gives the sub-task a stable identity so successive events for the
    same unit of work (e.g. "checking…" then "done") update one tree node, and
    parallel units (different ``node_id``) render as sibling nodes at the same level.
    """
    return progress_event(
        stage=stage or agent,
        title=title,
        status=status,
        kind="substep",
        detail=detail,
        parent=parent,
        agent=agent,
        node_id=node_id,
        parent_id=parent_id,
    )


def from_trace_event(event: TraceEvent) -> dict[str, Any]:
    detail_parts = [event.summary, event.input_preview, event.output_preview]
    detail = " · ".join(part.strip() for part in detail_parts if part and part.strip())
    return progress_event(
        stage=event.stage or event.actor or "agent",
        title=event.title or event.stage or "step",
        status=event.status or "running",
        kind=str(event.kind.value if hasattr(event.kind, "value") else event.kind),
        detail=detail[:500],
        duration_ms=float(event.duration_ms or 0.0),
    )


def progress_label(payload: str | dict[str, Any]) -> str:
    if isinstance(payload, str):
        return payload.strip()
    stage = str(payload.get("stage") or "").strip()
    title = str(payload.get("title") or "").strip()
    if stage == "build_assets" and title:
        from dbaide.i18n import localized_build_title
        title = localized_build_title(title)
    detail = str(payload.get("detail") or "").strip()
    if title and stage and title != stage and stage != "build_assets":
        text = f"{stage}: {title}"
    else:
        text = title or stage or detail or "Working…"
    if detail and detail not in {text, title, stage}:
        text = f"{text} — {detail[:80]}"
    return text[:240]


def brief_tool_summary(tool: str, result: Any) -> str:
    """Human-readable one-liner for live trace (not full JSON dump)."""
    if not getattr(result, "ok", False):
        err = getattr(result, "error", None)
        return f"Failed: {getattr(err, 'message', err) or 'unknown error'}"
    data = getattr(result, "data", None) or {}
    if not isinstance(data, dict):
        return "ok"
    if tool == "discover_schema":
        count = data.get("count", len(data.get("hits") or []))
        return f"{count} schema hit(s)"
    if tool == "retrieve_schema_context":
        return str(data.get("source_summary") or f"{len(data.get('candidates') or [])} candidate(s)")[:200]
    if tool == "describe_table":
        tables = data.get("disclosed_tables") or []
        cols = data.get("columns") or []
        if tables:
            return f"disclosed {', '.join(str(t) for t in tables)}"
        return f"{len(cols)} column(s)"
    if tool == "retrieve_join_context":
        return str(data.get("source_summary") or f"{len(data.get('relations') or [])} relation candidate(s)")[:200]
    if tool == "validate_joins":
        rels = data.get("relations") or []
        validated = data.get("validated_count", sum(1 for r in rels if r.get("validated")))
        types = sorted({str(r.get("join_type") or "") for r in rels if r.get("join_type")})
        suffix = f" ({', '.join(types)})" if types else ""
        return f"{validated}/{len(rels)} joins valid{suffix}"
    if tool == "generate_sql":
        sql = str(data.get("sql") or "").strip()
        tables = data.get("tables") or []
        prefix = f"tables={', '.join(str(t) for t in tables)} · " if tables else ""
        return prefix + (sql[:120] + "…" if len(sql) > 120 else sql or "SQL drafted")
    if tool == "validate_sql":
        return "valid" if data.get("ok") else "; ".join(
            issue.get("message", str(issue)) if isinstance(issue, dict) else str(issue)
            for issue in (data.get("issues") or [])[:3]
        ) or "invalid"
    if tool in {"execute_sql", "execute_readonly_sql"}:
        return f"{data.get('row_count', '?')} rows"
    if tool == "ask_user":
        return str(data.get("question") or "waiting for user")[:160]
    if tool == "profile_table":
        return f"{data.get('column_count', '?')} column profile(s)"
    if tool == "column_stats":
        cols = data.get("columns") or []
        names = ", ".join(str(c.get("column")) for c in cols[:6])
        return f"stats: {names}" + (" …" if len(cols) > 6 else "")
    if tool == "render_chart":
        title = str(data.get("title") or data.get("chart_type") or "chart").strip()
        return f"{title} ({data.get('row_count', '?')} pts)"
    raw = str(data)
    return raw[:200] + ("…" if len(raw) > 200 else "")


def normalize_trace_key(text: str) -> str:
    text = " ".join(str(text or "").strip().split())
    if " — " in text:
        text = text.split(" — ", 1)[0].strip()
    return text.lower()


def _stage_title(stage: str, title: str) -> str:
    """Combine a stage id and a human title without doubling — when the title
    already echoes the stage, the stage prefix is dropped."""
    if not title:
        return stage
    if not stage:
        return title
    t, s = title.lower(), stage.lower()
    if t == s:
        return title
    return f"{stage}: {title}"


def conversation_trace_step(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Map a progress or persisted trace dict to (message, kind, detail)."""
    stage = str(event.get("stage") or "").strip()
    title = str(event.get("title") or "").strip()
    summary = str(event.get("summary") or "").strip()
    output = str(event.get("output_preview") or "").strip()
    detail = str(event.get("detail") or "").strip()
    status = str(event.get("status") or "").strip()
    kind = str(event.get("kind") or "").strip()
    actor = str(event.get("actor") or "").strip()

    if stage in {"workflow_started", "planning"}:
        return None

    if status == "info" or kind == "substep":
        line = title or summary or detail
        agent_name = str(event.get("agent") or "").strip()
        parent = str(event.get("parent") or "").strip()
        if agent_name and line and line.removeprefix(f"{agent_name}:") == line:
            line = f"{agent_name}: {line}"
        elif parent and line and parent not in line:
            line = f"{parent} › {line}"
        return (line, "info", detail if detail != line else "") if line else None

    if stage in TOOL_TRACE_STAGES or actor == "tool":
        message = _stage_title(stage, title) if (stage or title) else (summary or stage)
        step_detail = output or detail
        if summary and summary not in message and summary != title:
            step_detail = summary if not step_detail else step_detail
        return message, "tool", step_detail

    if stage == "sql_generated":
        return title or "SQL generated", "decision", output or detail
    if stage == "sql_validation":
        return title or "Validating SQL", "result", output or detail
    if stage in {"execution_completed", "execute_sql", "execute_readonly_sql"}:
        return title or summary or stage, "result", output or summary or detail
    if stage in {"workflow_completed", "result_interpreted", "waiting_for_user"}:
        return title or summary or stage, "info", detail or summary

    if stage == "agent_progress":
        line = summary or title
        return (line, kind or "info", detail) if line else None

    if title or summary:
        message = _stage_title(stage, title) if (stage or title) else (summary or stage)
        step_kind = kind or ("tool" if stage in TOOL_TRACE_STAGES else "info")
        step_detail = output or detail or (summary if summary != message else "")
        return message, step_kind, step_detail

    return None


def _node_namespace(node_id: str) -> str:
    return str(node_id or "").split(":", 1)[0]
