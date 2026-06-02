"""Serialize / restore agent tool-loop state for ask_user resume."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from dbaide.agent.progressive_schema import DiscoveryResult, SchemaHit
from dbaide.models import ColumnInfo

LOOP_STATE_VERSION = 1


def column_to_dict(col: ColumnInfo) -> dict[str, Any]:
    return {
        "name": col.name,
        "data_type": col.data_type,
        "nullable": col.nullable,
        "default": col.default,
        "comment": col.comment,
        "primary_key": col.primary_key,
        "indexed": col.indexed,
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
    hits = [SchemaHit(**item) for item in data.get("hits") or []]
    return DiscoveryResult(
        question=str(data.get("question") or ""),
        hits=hits,
        trace=list(data.get("trace") or []),
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
        for key, cols in orchestrator._loop_schemas.items()
    }
    columns = [column_to_dict(col) for col in orchestrator._loop_columns]
    return {
        "version": LOOP_STATE_VERSION,
        "question": orchestrator._loop_question,
        "database": orchestrator._loop_database,
        "execute_allowed": execute_allowed,
        "transcript": list(transcript),
        "orchestrator": {
            "_loop_discovery": discovery_to_dict(orchestrator._loop_discovery),
            "_loop_table": orchestrator._loop_table,
            "_loop_table_database": orchestrator._loop_table_database,
            "_loop_columns": columns,
            "_loop_schemas": schemas,
            "_loop_schema_db": dict(orchestrator._loop_schema_db),
            "_loop_relations": list(orchestrator._loop_relations),
            "_loop_sql": orchestrator._loop_sql,
            "_loop_sql_rationale": orchestrator._loop_sql_rationale,
            "_loop_sql_confidence": orchestrator._loop_sql_confidence,
            "_loop_sql_feedback": orchestrator._loop_sql_feedback,
            "_loop_answer": orchestrator._loop_answer,
            "_loop_clarifications": list(getattr(orchestrator, "_loop_clarifications", [])),
            "_loop_clarify_questions": getattr(orchestrator, "_loop_clarify_questions", ""),
        },
    }


def restore_loop_state(orchestrator: Any, snapshot: dict[str, Any]) -> tuple[list[str], bool]:
    """Restore orchestrator loop fields. Returns (transcript, execute_allowed)."""
    orchestrator._loop_question = str(snapshot.get("question") or "")
    orchestrator._loop_database = str(snapshot.get("database") or "")
    execute_allowed = bool(snapshot.get("execute_allowed", True))
    transcript = list(snapshot.get("transcript") or [])
    payload = snapshot.get("orchestrator") or {}

    orchestrator._loop_discovery = discovery_from_dict(payload.get("_loop_discovery"))
    orchestrator._loop_table = str(payload.get("_loop_table") or "")
    orchestrator._loop_table_database = str(payload.get("_loop_table_database") or "")
    orchestrator._loop_columns = [column_from_dict(item) for item in payload.get("_loop_columns") or []]
    orchestrator._loop_schemas = {
        key: [column_from_dict(item) for item in cols]
        for key, cols in (payload.get("_loop_schemas") or {}).items()
    }
    orchestrator._loop_schema_db = dict(payload.get("_loop_schema_db") or {})
    orchestrator._loop_relations = list(payload.get("_loop_relations") or [])
    orchestrator._loop_sql = str(payload.get("_loop_sql") or "")
    orchestrator._loop_sql_rationale = str(payload.get("_loop_sql_rationale") or "")
    orchestrator._loop_sql_confidence = float(payload.get("_loop_sql_confidence") or 0.0)
    orchestrator._loop_sql_feedback = str(payload.get("_loop_sql_feedback") or "")
    orchestrator._loop_answer = str(payload.get("_loop_answer") or "")
    orchestrator._loop_clarifications = list(payload.get("_loop_clarifications") or [])
    orchestrator._loop_clarify_questions = str(payload.get("_loop_clarify_questions") or "")
    orchestrator._loop_execute_allowed = execute_allowed
    orchestrator._loop_query_result = None
    orchestrator._loop_pending_question = ""
    orchestrator._loop_pending_options = []
    return transcript, execute_allowed
