"""Serialize / restore agent tool-loop state for ask_user resume."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from dbaide.agent.memory import AgentMemory
from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit
from dbaide.i18n import normalize
from dbaide.models import ColumnInfo, QueryResult

LOOP_STATE_VERSION = 3


def column_to_dict(col: ColumnInfo) -> dict[str, Any]:
    return {
        "name": col.name,
        "data_type": col.data_type,
        "nullable": col.nullable,
        "default": col.default,
        "comment": col.comment,
        "primary_key": col.primary_key,
        "indexed": col.indexed,
        "note": col.note,
    }


def column_from_dict(data: dict[str, Any]) -> ColumnInfo:
    return ColumnInfo(
        name=str(data.get("name") or ""),
        data_type=str(data.get("data_type") or ""),
        nullable=data.get("nullable"),
        default=data.get("default"),
        comment=str(data.get("comment") or ""),
        primary_key=bool(data.get("primary_key")),
        indexed=bool(data.get("indexed")),
        note=str(data.get("note") or ""),
    )


def discovery_to_dict(discovery: DiscoveryResult | None) -> dict[str, Any] | None:
    if discovery is None:
        return None
    return {
        "question": discovery.question,
        "hits": [asdict(hit) for hit in discovery.hits],
        "trace": list(discovery.trace),
    }


def discovery_from_dict(data: dict[str, Any] | None) -> DiscoveryResult | None:
    if not data:
        return None
    hits = [_schema_hit_from_dict(item) for item in data.get("hits") or [] if isinstance(item, dict)]
    return DiscoveryResult(
        question=str(data.get("question") or ""),
        hits=hits,
        trace=[str(x) for x in _list_or_empty(data.get("trace"))],
    )


def _schema_hit_from_dict(data: dict[str, Any]) -> SchemaHit:
    return SchemaHit(
        kind=str(data.get("kind") or ""),
        path=str(data.get("path") or ""),
        name=str(data.get("name") or ""),
        database=str(data.get("database") or ""),
        table=str(data.get("table") or ""),
        summary=str(data.get("summary") or ""),
        reason=str(data.get("reason") or ""),
        note=str(data.get("note") or ""),
    )


def dump_loop_state(
    orchestrator: Any,
    *,
    transcript: list[str],
    execute_allowed: bool,
) -> dict[str, Any]:
    """Capture loop context so a later user reply can resume the same run."""
    schemas = {
        key: [column_to_dict(col) for col in cols]
        for key, cols in orchestrator.run_state.schemas.items()
    }
    columns = [column_to_dict(col) for col in orchestrator.run_state.columns]
    return {
        "version": LOOP_STATE_VERSION,
        "question": orchestrator.run_state.question,
        "database": orchestrator.run_state.database,
        "execute_allowed": execute_allowed,
        "answer_language": orchestrator.run_state.answer_language,
        "transcript": list(transcript),
        "run_state": {
            "discovery": discovery_to_dict(orchestrator.run_state.discovery),
            "table": orchestrator.run_state.table,
            "table_database": orchestrator.run_state.table_database,
            "columns": columns,
            "schemas": schemas,
            "schema_db": dict(orchestrator.run_state.schema_db),
            "relations": list(orchestrator.run_state.relations),
            "sql": orchestrator.run_state.sql,
            "sql_rationale": orchestrator.run_state.sql_rationale,
            "sql_confidence": orchestrator.run_state.sql_confidence,
            "sql_feedback": orchestrator.run_state.sql_feedback,
            "answer": orchestrator.run_state.answer,
            "pending_question": orchestrator.run_state.pending_question,
            "pending_options": list(orchestrator.run_state.pending_options),
            "pending_questions": list(orchestrator.run_state.pending_questions),
            "risk_confirmation": dict(orchestrator.run_state.risk_confirmation),
            "confirmed_risk_sqls": list(orchestrator.run_state.confirmed_risk_sqls),
            "clarifications": list(orchestrator.run_state.clarifications),
            "clarify_questions": orchestrator.run_state.clarify_questions,
            "memory": orchestrator.run_state.memory.to_dict(),
            "scope_used": bool(orchestrator.run_state.scope_used),
            "schema_prefetched": bool(orchestrator.run_state.schema_prefetched),
            "query_result": query_result_to_dict(orchestrator.run_state.query_result),
            "charts": list(orchestrator.run_state.charts or []),
            "executed_sqls": [
                dict(item)
                for item in (orchestrator.run_state.executed_sqls or [])
                if isinstance(item, dict)
            ],
        },
    }


def query_result_to_dict(result: QueryResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "columns": list(result.columns),
        "rows": list(result.rows),
        "row_count": int(result.row_count),
        "truncated": bool(result.truncated),
        "sql": str(result.sql or ""),
        "elapsed_ms": float(result.elapsed_ms or 0.0),
    }


def query_result_from_dict(data: dict[str, Any] | None) -> QueryResult | None:
    if not isinstance(data, dict):
        return None
    return QueryResult(
        columns=[str(x) for x in _list_or_empty(data.get("columns"))],
        rows=[row for row in _list_or_empty(data.get("rows")) if isinstance(row, dict)],
        row_count=int(data.get("row_count") or 0),
        truncated=bool(data.get("truncated")),
        sql=str(data.get("sql") or ""),
        elapsed_ms=float(data.get("elapsed_ms") or 0.0),
    )


def restore_loop_state(orchestrator: Any, snapshot: dict[str, Any]) -> tuple[list[str], bool]:
    """Restore orchestrator loop fields. Returns (transcript, execute_allowed)."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    orchestrator.run_state.question = str(snapshot.get("question") or "")
    orchestrator.run_state.database = str(snapshot.get("database") or "")
    orchestrator.run_state.answer_language = normalize(snapshot.get("answer_language") or "en")
    execute_allowed = bool(snapshot.get("execute_allowed", True))
    transcript = [str(x) for x in _list_or_empty(snapshot.get("transcript"))]
    payload = snapshot.get("run_state") if isinstance(snapshot.get("run_state"), dict) else {}

    orchestrator.run_state.discovery = discovery_from_dict(payload.get("discovery"))
    orchestrator.run_state.table = str(payload.get("table") or "")
    orchestrator.run_state.table_database = str(payload.get("table_database") or "")
    orchestrator.run_state.columns = [
        column_from_dict(item) for item in _list_or_empty(payload.get("columns")) if isinstance(item, dict)
    ]
    schemas_payload = payload.get("schemas") if isinstance(payload.get("schemas"), dict) else {}
    orchestrator.run_state.schemas = {
        str(key): [column_from_dict(item) for item in cols if isinstance(item, dict)]
        for key, cols in schemas_payload.items()
        if isinstance(cols, list)
    }
    orchestrator.run_state.schema_db = _dict_or_empty(payload.get("schema_db"))
    orchestrator.run_state.relations = _list_or_empty(payload.get("relations"))
    orchestrator.run_state.sql = str(payload.get("sql") or "")
    orchestrator.run_state.sql_rationale = str(payload.get("sql_rationale") or "")
    _conf = payload.get("sql_confidence")
    orchestrator.run_state.sql_confidence = _float_or_none(_conf)
    orchestrator.run_state.sql_feedback = str(payload.get("sql_feedback") or "")
    orchestrator.run_state.answer = str(payload.get("answer") or "")
    orchestrator.run_state.pending_question = str(payload.get("pending_question") or "")
    orchestrator.run_state.pending_options = _list_or_empty(payload.get("pending_options"))
    orchestrator.run_state.pending_questions = _list_or_empty(payload.get("pending_questions"))
    orchestrator.run_state.risk_confirmation = _dict_or_empty(payload.get("risk_confirmation"))
    orchestrator.run_state.confirmed_risk_sqls = [str(x) for x in _list_or_empty(payload.get("confirmed_risk_sqls"))]
    orchestrator.run_state.clarifications = [str(x) for x in _list_or_empty(payload.get("clarifications"))]
    orchestrator.run_state.clarify_questions = str(payload.get("clarify_questions") or "")
    orchestrator.run_state.memory = AgentMemory.from_dict(payload.get("memory"))
    orchestrator.run_state.scope_used = bool(payload.get("scope_used", False))
    orchestrator.run_state.schema_prefetched = bool(payload.get("schema_prefetched", False))
    orchestrator.run_state.query_result = query_result_from_dict(payload.get("query_result"))
    charts_payload = payload.get("charts")
    orchestrator.run_state.charts = [
        dict(item) for item in _list_or_empty(charts_payload) if isinstance(item, dict)
    ]
    executed_payload = payload.get("executed_sqls")
    orchestrator.run_state.executed_sqls = [
        dict(item) for item in _list_or_empty(executed_payload) if isinstance(item, dict)
    ]
    orchestrator.run_state.execute_allowed = execute_allowed
    if not orchestrator.run_state.memory.goal and orchestrator.run_state.question:
        orchestrator.run_state.memory.reset_goal(
            orchestrator.run_state.question,
            database=orchestrator.run_state.database,
            execute_allowed=execute_allowed,
        )
    return transcript, execute_allowed


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _list_or_empty(value: Any) -> list:
    return list(value) if isinstance(value, list) else []


def _dict_or_empty(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}
