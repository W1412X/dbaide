"""Serialize / restore agent tool-loop state for ask_user resume."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from dbaide.agent.memory import AgentMemory
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
        for key, cols in orchestrator.run_state.schemas.items()
    }
    columns = [column_to_dict(col) for col in orchestrator.run_state.columns]
    return {
        "version": LOOP_STATE_VERSION,
        "question": orchestrator.run_state.question,
        "database": orchestrator.run_state.database,
        "execute_allowed": execute_allowed,
        "transcript": list(transcript),
        "orchestrator": {
            "_loop_discovery": discovery_to_dict(orchestrator.run_state.discovery),
            "_loop_table": orchestrator.run_state.table,
            "_loop_table_database": orchestrator.run_state.table_database,
            "_loop_columns": columns,
            "_loop_schemas": schemas,
            "_loop_schema_db": dict(orchestrator.run_state.schema_db),
            "_loop_relations": list(orchestrator.run_state.relations),
            "_loop_sql": orchestrator.run_state.sql,
            "_loop_sql_rationale": orchestrator.run_state.sql_rationale,
            "_loop_sql_confidence": orchestrator.run_state.sql_confidence,
            "_loop_sql_feedback": orchestrator.run_state.sql_feedback,
            "_loop_answer": orchestrator.run_state.answer,
            "_loop_risk_confirmation": dict(orchestrator.run_state.risk_confirmation),
            "_loop_confirmed_risk_sqls": list(orchestrator.run_state.confirmed_risk_sqls),
            "_loop_clarifications": list(orchestrator.run_state.clarifications),
            "_loop_clarify_questions": orchestrator.run_state.clarify_questions,
            "_loop_memory": orchestrator.run_state.memory.to_dict(),
        },
    }


def restore_loop_state(orchestrator: Any, snapshot: dict[str, Any]) -> tuple[list[str], bool]:
    """Restore orchestrator loop fields. Returns (transcript, execute_allowed)."""
    orchestrator.run_state.question = str(snapshot.get("question") or "")
    orchestrator.run_state.database = str(snapshot.get("database") or "")
    execute_allowed = bool(snapshot.get("execute_allowed", True))
    transcript = list(snapshot.get("transcript") or [])
    payload = snapshot.get("orchestrator") or {}

    orchestrator.run_state.discovery = discovery_from_dict(payload.get("_loop_discovery"))
    orchestrator.run_state.table = str(payload.get("_loop_table") or "")
    orchestrator.run_state.table_database = str(payload.get("_loop_table_database") or "")
    orchestrator.run_state.columns = [column_from_dict(item) for item in payload.get("_loop_columns") or []]
    orchestrator.run_state.schemas = {
        key: [column_from_dict(item) for item in cols]
        for key, cols in (payload.get("_loop_schemas") or {}).items()
    }
    orchestrator.run_state.schema_db = dict(payload.get("_loop_schema_db") or {})
    orchestrator.run_state.relations = list(payload.get("_loop_relations") or [])
    orchestrator.run_state.sql = str(payload.get("_loop_sql") or "")
    orchestrator.run_state.sql_rationale = str(payload.get("_loop_sql_rationale") or "")
    _conf = payload.get("_loop_sql_confidence")
    orchestrator.run_state.sql_confidence = None if _conf is None else float(_conf)
    orchestrator.run_state.sql_feedback = str(payload.get("_loop_sql_feedback") or "")
    orchestrator.run_state.answer = str(payload.get("_loop_answer") or "")
    orchestrator.run_state.risk_confirmation = dict(payload.get("_loop_risk_confirmation") or {})
    orchestrator.run_state.confirmed_risk_sqls = list(payload.get("_loop_confirmed_risk_sqls") or [])
    orchestrator.run_state.clarifications = list(payload.get("_loop_clarifications") or [])
    orchestrator.run_state.clarify_questions = str(payload.get("_loop_clarify_questions") or "")
    orchestrator.run_state.memory = AgentMemory.from_dict(payload.get("_loop_memory"))
    orchestrator.run_state.execute_allowed = execute_allowed
    orchestrator.run_state.query_result = None
    orchestrator.run_state.pending_question = ""
    orchestrator.run_state.pending_options = []
    orchestrator.run_state.pending_questions = []
    return transcript, execute_allowed
