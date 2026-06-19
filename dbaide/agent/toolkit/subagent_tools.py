"""Child-agent delegation tool."""
from __future__ import annotations

import json
from typing import Any

from dbaide.step_budget import clamp_agent_max_steps, child_step_budget
from dbaide.charts.embed import merge_chart_specs, remap_chart_refs
from dbaide.agent.toolkit.result_preview import preview_rows
from dbaide.agent.toolkit.support import _err, _string_list
from dbaide.models import AssistantResponse
from dbaide.session import Session
from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import RUN_SUBAGENT


def register(registry: ToolRegistry, orchestrator) -> None:
    def _run_subagent(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        task = str(args.get("task") or "").strip()
        if not task:
            return ToolResult(ok=False, error=_err("run_subagent", "task is required"))
        if getattr(orchestrator, "subagent_depth", 0) >= getattr(orchestrator, "max_subagent_depth", 1):
            return ToolResult(
                ok=False,
                error=_err("run_subagent", "subagent depth limit reached", retryable=False),
            )
        context = str(args.get("context") or "").strip()
        context_refs = _string_list(args.get("context_refs"))
        deliverables = _string_list(args.get("deliverables"))
        allowed_tools = _string_list(args.get("allowed_tools"))
        database = str(
            args.get("database")
            or orchestrator.run_state.table_database
            or orchestrator.run_state.database
            or ""
        ).strip()
        execute = bool(args.get("execute", True))
        if not orchestrator.run_state.execute_allowed:
            execute = False
        child_steps = child_step_budget(args.get("max_steps"), orchestrator.session.agent_max_steps)
        child_session = _child_session(orchestrator.session, max_steps=child_steps)

        from dbaide.agent.loop import AskAgentLoop
        from dbaide.agent.orchestrator import AskOrchestrator
        from dbaide.agent.progress_events import progress_event

        parent_node = orchestrator.run_state.trace_node or "run_subagent"
        child_id = _subagent_id(orchestrator)
        child_parent = f"{parent_node}:{child_id}"
        orchestrator.progress(progress_event(
            stage="run_subagent",
            title=f"Subagent: {task[:80]}",
            status="running",
            kind="agent",
            node_id=child_parent,
            parent_id=parent_node,
            detail=context[:240],
        ))

        def child_progress(event: Any) -> None:
            if not isinstance(event, dict):
                orchestrator.progress(event)
                return
            event = dict(event)
            if event.get("node_id") and not str(event["node_id"]).startswith(child_parent):
                event["node_id"] = f"{child_parent}:{event['node_id']}"
            if event.get("parent_id") and not str(event["parent_id"]).startswith(child_parent):
                event["parent_id"] = f"{child_parent}:{event['parent_id']}"
            elif not event.get("parent_id"):
                event["parent_id"] = child_parent
            orchestrator.progress(event)

        child = AskOrchestrator(
            orchestrator.adapter,
            child_session,
            orchestrator.llm,
            asset_store=orchestrator.asset_store,
            join_catalog=orchestrator.join_catalog,
            annotations=orchestrator.annotations,
            progress=child_progress,
            model_config=orchestrator.model_config,
        )
        child.subagent_depth = getattr(orchestrator, "subagent_depth", 0) + 1
        child.max_subagent_depth = getattr(orchestrator, "max_subagent_depth", 1)
        child.tool_allowlist = set(allowed_tools) if allowed_tools else None
        child.schema_scope = dict(getattr(orchestrator, "schema_scope", {}) or {})
        child.stream_answers = False
        child.cancel_check = orchestrator.cancel_check
        child.session_turns = list(getattr(orchestrator, "session_turns", []) or [])
        child.active_criteria = list(orchestrator.run_state.clarifications or [])

        child_question = task
        child_context = _child_context(orchestrator, context, context_refs, deliverables)
        if child_context:
            child_question += (
                "\n\nParent context and constraints:\n"
                f"{child_context}\n\n"
                "Solve only this delegated subtask. Return concise findings for the parent agent."
            )
        try:
            response = AskAgentLoop(child, progress=child_progress).run(
                child_question,
                database=database,
                execute=execute,
                trace_parent=child_parent,
                answer_language=orchestrator.run_state.answer_language,
            )
        except Exception as exc:
            orchestrator.progress(progress_event(
                stage="run_subagent",
                title=f"Subagent failed: {task[:80]}",
                status="failed",
                kind="agent",
                node_id=child_parent,
                parent_id=parent_node,
                detail=str(exc)[:240],
            ))
            return ToolResult(ok=False, error=_err("run_subagent", str(exc), retryable=True))

        _merge_child_state(orchestrator, child, response)
        data = _response_payload(task, response, deliverables=deliverables, evidence_refs=context_refs)
        orchestrator.progress(progress_event(
            stage="run_subagent",
            title=f"Subagent done: {task[:80]}",
            status="completed" if response.status != "wait_user" else "waiting",
            kind="agent",
            node_id=child_parent,
            parent_id=parent_node,
            detail=str(response.answer or "")[:240],
        ))
        return ToolResult(ok=True, data=data)

    registry.register(RUN_SUBAGENT, _run_subagent)


def _child_session(parent: Session, *, max_steps: int) -> Session:
    return Session(
        connection=parent.connection,
        disclosure=parent.disclosure,
        default_limit=parent.default_limit,
        timeout_seconds=parent.timeout_seconds,
        agent_max_steps=clamp_agent_max_steps(max_steps),
        prior_turns_window=parent.prior_turns_window,
        max_batch_tools=parent.max_batch_tools,
        latest_result_limit=parent.latest_result_limit,
        compress_threshold=parent.compress_threshold,
        session_uncompressed_turns=parent.session_uncompressed_turns,
    )


def _subagent_id(orchestrator) -> str:
    current = int(getattr(orchestrator.run_state, "_subagent_count", 0) or 0) + 1
    setattr(orchestrator.run_state, "_subagent_count", current)
    return f"subagent:{current}"


def _merge_child_state(parent, child, response: AssistantResponse) -> None:
    parent.run_state.memory.verified_facts.extend(
        fact for fact in child.run_state.memory.verified_facts
        if fact not in parent.run_state.memory.verified_facts
    )
    for key, columns in child.run_state.schemas.items():
        if key not in parent.run_state.schemas:
            parent.run_state.schemas[key] = list(columns)
            parent.run_state.schema_db[key] = child.run_state.schema_db.get(key, "")
    for fact in child.run_state.clarifications:
        if fact and fact not in parent.run_state.clarifications:
            parent.run_state.clarifications.append(fact)
    _merge_child_charts(parent, response)
    for item in child.run_state.executed_sqls:
        _append_child_execution(parent, item)
    if response.sql and not parent.run_state.sql:
        parent.run_state.sql = response.sql


def _response_payload(
    task: str,
    response: AssistantResponse,
    *,
    deliverables: list[str],
    evidence_refs: list[str],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    row_meta: dict[str, Any] = {}
    result = response.result
    if result is not None:
        rows, row_meta = preview_rows(
            list(result.rows or []),
            columns=list(result.columns or []),
            max_rows=10,
        )
    return {
        "task": task,
        "status": response.status,
        "answer": response.answer,
        "sql": response.sql,
        "result_preview": rows,
        "row_preview": row_meta,
        "warnings": list(response.warnings or []),
        "charts": list(response.charts or []),
        "executed_sqls": list(response.executed_sqls or []),
        "pending_question": response.pending_question,
        "pending_options": list(response.pending_options or []),
        "deliverables": list(deliverables),
        "verified_facts": _verified_fact_lines(response.answer),
        "evidence_refs": list(evidence_refs),
    }


def _append_child_execution(parent, item: dict[str, Any]) -> None:
    if not isinstance(item, dict):
        return
    key = _execution_key(item)
    existing = {_execution_key(entry) for entry in (parent.run_state.executed_sqls or []) if isinstance(entry, dict)}
    if key in existing:
        return
    entry = dict(item)
    entry["index"] = len(parent.run_state.executed_sqls or []) + 1
    parent.run_state.executed_sqls.append(entry)


def _execution_key(item: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(item.get("sql") or "").strip(),
        str(item.get("purpose") or "").strip(),
        str(item.get("database") or "").strip(),
        str(item.get("tool") or "").strip(),
        str(item.get("artifact_id") or "").strip(),
    )


def _merge_child_charts(parent, response: AssistantResponse) -> None:
    parent_charts = [dict(item) for item in (parent.run_state.charts or []) if isinstance(item, dict)]
    child_charts, id_map = merge_chart_specs(parent_charts, response.charts)
    if not child_charts:
        return
    if id_map:
        response.answer = remap_chart_refs(response.answer, id_map)
    response.charts = child_charts
    parent.run_state.charts = parent_charts + child_charts


def _child_context(orchestrator, context: str, context_refs: list[str], deliverables: list[str]) -> str:
    parts: list[str] = []
    if context:
        parts.append(context)
    if deliverables:
        parts.append("Expected deliverables: " + ", ".join(deliverables))
    if orchestrator.run_state.agenda:
        parts.append("Parent task list: " + json.dumps([
            {"id": item.id, "title": item.title, "status": item.status}
            for item in orchestrator.run_state.agenda
        ], ensure_ascii=False))
    for ref in context_refs:
        snippet = _resolve_context_ref(orchestrator, ref)
        if snippet:
            parts.append(f"[{ref}]\n{snippet}")
    return "\n\n".join(part for part in parts if part.strip())


def _resolve_context_ref(orchestrator, ref: str) -> str:
    text = str(ref or "").strip()
    if not text:
        return ""
    if text == "current_sql":
        sql = str(orchestrator.run_state.sql or "").strip()
        return f"```sql\n{sql}\n```" if sql else ""
    if text == "current_result":
        result = orchestrator.run_state.query_result
        if result is None:
            return ""
        preview, meta = preview_rows(
            list(result.rows or []),
            columns=list(result.columns or []),
            max_rows=8,
        )
        payload = {
            "columns": list(result.columns or []),
            "row_count": int(result.row_count or 0),
            "rows": preview,
            "preview_meta": meta,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if text == "current_schema":
        payload = []
        for key, cols in (orchestrator.run_state.schemas or {}).items():
            payload.append({
                "table": key,
                "columns": [
                    {"name": getattr(col, "name", ""), "data_type": getattr(col, "data_type", "")}
                    for col in cols
                ],
            })
        return json.dumps(payload, ensure_ascii=False, indent=2) if payload else ""
    if text == "current_relations":
        relations = list(orchestrator.run_state.relations or [])
        return json.dumps(relations, ensure_ascii=False, indent=2) if relations else ""
    if text.startswith("turn:"):
        turn_id = text.split(":", 1)[1].strip()
        idx = _turn_index(orchestrator, turn_id)
        if idx < 0:
            return ""
        turn = orchestrator.session_turns[idx]
        payload = {
            "turn_id": turn_id,
            "question": turn.get("question"),
            "answer": turn.get("answer_markdown"),
            "selected_sql": turn.get("selected_sql"),
            "executed_sqls": turn.get("executed_sqls"),
            "clarifications": turn.get("clarifications"),
            "verified_facts": turn.get("verified_facts"),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if text.startswith("artifact:"):
        artifact_id = text.split(":", 1)[1].strip()
        for item in reversed(orchestrator.run_state.memory.sql_artifacts or []):
            if str(getattr(item, "id", "") or "") == artifact_id:
                return json.dumps({
                    "id": item.id,
                    "purpose": item.purpose,
                    "sql": item.sql,
                    "row_count": item.row_count,
                    "columns": list(item.columns or []),
                    "rows_preview": list(item.rows_preview or []),
                    "warnings": list(item.warnings or []),
                }, ensure_ascii=False, indent=2)
        return ""
    return ""


def _turn_index(orchestrator, turn_id: str) -> int:
    text = str(turn_id or "").strip().lower()
    if not text.startswith("t"):
        return -1
    try:
        idx = int(text[1:]) - 1
    except ValueError:
        return -1
    turns = orchestrator.session_turns or []
    return idx if 0 <= idx < len(turns) else -1


def _verified_fact_lines(answer: str) -> list[str]:
    facts: list[str] = []
    for line in str(answer or "").splitlines():
        text = line.strip().lstrip("-").strip()
        if text and len(text) <= 240:
            facts.append(text)
        if len(facts) >= 6:
            break
    return facts
