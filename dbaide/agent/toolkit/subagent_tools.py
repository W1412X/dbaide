"""Child-agent delegation tool."""
from __future__ import annotations

from typing import Any

from dbaide.agent.toolkit.result_preview import preview_rows
from dbaide.agent.toolkit.support import _err
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
        database = str(
            args.get("database")
            or orchestrator.run_state.table_database
            or orchestrator.run_state.database
            or ""
        ).strip()
        execute = bool(args.get("execute", True))
        if not orchestrator.run_state.execute_allowed:
            execute = False
        child_steps = _child_step_budget(args.get("max_steps"), orchestrator.session.agent_max_steps)
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
        child.schema_scope = dict(getattr(orchestrator, "schema_scope", {}) or {})
        child.stream_answers = False
        child.cancel_check = orchestrator.cancel_check
        child.session_turns = list(getattr(orchestrator, "session_turns", []) or [])
        child.active_criteria = list(orchestrator.run_state.clarifications or [])

        child_question = task
        if context:
            child_question += (
                "\n\nParent context and constraints:\n"
                f"{context}\n\n"
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
        data = _response_payload(task, response)
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
        agent_max_steps=max_steps,
        prior_turns_window=parent.prior_turns_window,
        max_batch_tools=parent.max_batch_tools,
        latest_result_limit=parent.latest_result_limit,
        compress_threshold=parent.compress_threshold,
    )


def _child_step_budget(value: Any, parent_max: int) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = min(24, max(4, int(parent_max or 64) // 2))
    return max(4, min(requested, int(parent_max or 64), 32))


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
    for item in child.run_state.executed_sqls:
        if item not in parent.run_state.executed_sqls:
            parent.run_state.executed_sqls.append(dict(item))
    if response.sql and not parent.run_state.sql:
        parent.run_state.sql = response.sql


def _response_payload(task: str, response: AssistantResponse) -> dict[str, Any]:
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
        "executed_sqls": list(response.executed_sqls or []),
    }
