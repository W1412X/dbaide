"""Relations, join catalog and user-annotation tools."""
from __future__ import annotations

from typing import Any

from dbaide.tools.registry import ToolContext, ToolRegistry, ToolResult
from dbaide.tools.specs import (
    RETRIEVE_JOIN_CONTEXT, VALIDATE_JOINS, LIST_JOINS,
    ADD_JOIN, UPDATE_JOIN, DELETE_JOIN, ANNOTATE_OBJECT,
)
from dbaide.agent.schema_context import disclosed_schemas_for_tables
from dbaide.agent.join_validation import validate_join_relations
from dbaide.joins import USER_JOIN_CONFIDENCE, catalog_record_to_relation
from dbaide.agent.toolkit.support import _err, _relations_payload, _targets_from_relations


def register(registry: ToolRegistry, orchestrator) -> None:
    def _retrieve_join_context(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        from dbaide.agent.join_evidence import JoinEvidenceRetriever

        request = str(args.get("request") or orchestrator.run_state.question or "").strip()
        tables_arg = args.get("tables")
        tables = [str(t).strip() for t in tables_arg if str(t).strip()] if isinstance(tables_arg, list) else []
        database = str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")
        # Default to catalog/FK evidence only. Semantic inference and sample validation
        # are extra work and must be explicitly requested by the main LLM.
        infer_semantic = bool(args.get("infer_semantic", False))
        validate_sample = bool(args.get("validate_sample", False))
        sample_size = int(args.get("sample_size") or 150)
        try:
            report = JoinEvidenceRetriever(orchestrator).retrieve(
                request,
                tables=tables,
                database=database,
                infer_semantic=infer_semantic,
                validate_sample=validate_sample,
                sample_size=sample_size,
            )
        except Exception as exc:
            return ToolResult(ok=False, error=_err("retrieve_join_context", str(exc), retryable=True))
        return ToolResult(ok=True, data=report.to_tool_data())

    def _validate_joins(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        relations = list(orchestrator.run_state.relations or [])
        if not relations:
            return ToolResult(ok=False, error=_err("validate_joins", "no relations; call retrieve_join_context first"))
        targets = _targets_from_relations(orchestrator, relations)
        if len({t for _, t in targets}) < 2:
            return ToolResult(ok=False, error=_err("validate_joins", "need at least two tables"))
        schemas = disclosed_schemas_for_tables(orchestrator, targets)
        sample_size = int(args.get("sample_size") or 150)
        validated = validate_join_relations(
            orchestrator,
            relations,
            schemas,
            sample_size=sample_size,
            parent="validate_joins",
        )
        orchestrator.run_state.relations = validated
        return ToolResult(ok=True, data=_relations_payload(validated))

    def _list_joins(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        database = str(args.get("database") or orchestrator.run_state.database or "")
        tables_arg = args.get("tables")
        tables: list[str] | None = None
        if isinstance(tables_arg, list):
            tables = [str(t).strip() for t in tables_arg if str(t).strip()]
        min_conf = float(args.get("min_confidence") or 0.0)
        endpoint = args if args.get("table") and args.get("column") else None
        records = orchestrator.join_catalog.list_records(
            orchestrator.instance,
            database=database,
            tables=tables,
            min_confidence=min_conf,
            endpoint=endpoint,
            fingerprint=getattr(orchestrator, "connection_fingerprint", ""),
        )
        return ToolResult(ok=True, data={"joins": records, "count": len(records)})

    def _add_join(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        required = ("table", "column", "ref_table", "ref_column")
        missing = [k for k in required if not str(args.get(k) or "").strip()]
        if missing:
            return ToolResult(ok=False, error=_err("add_join", f"missing: {', '.join(missing)}"))
        database = str(args.get("database") or orchestrator.run_state.database or "")
        source = str(args.get("source") or "user").strip().lower()
        if source not in {"user", "agent"}:
            source = "user"
        rel = {
            "table": str(args["table"]).strip(),
            "column": str(args["column"]).strip(),
            "ref_table": str(args["ref_table"]).strip(),
            "ref_column": str(args["ref_column"]).strip(),
            "join_type": str(args.get("join_type") or ""),
            "reason": str(args.get("reason") or ""),
            "confidence": USER_JOIN_CONFIDENCE if source == "user" else float(args.get("confidence") or 0.7),
        }
        record = orchestrator.join_catalog.add(
            orchestrator.instance,
            rel,
            source=source,
            database=database,
            fingerprint=getattr(orchestrator, "connection_fingerprint", ""),
        )
        return ToolResult(ok=True, data={"join": record, "relation": catalog_record_to_relation(record)})

    def _update_join(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        join_id = str(args.get("id") or args.get("join_id") or "").strip()
        if not join_id:
            return ToolResult(ok=False, error=_err("update_join", "id is required"))
        updated = orchestrator.join_catalog.update(
            orchestrator.instance,
            join_id,
            args,
            fingerprint=getattr(orchestrator, "connection_fingerprint", ""),
        )
        if updated is None:
            return ToolResult(ok=False, error=_err("update_join", f"join not found: {join_id}"))
        return ToolResult(ok=True, data={"join": updated, "relation": catalog_record_to_relation(updated)})

    def _delete_join(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        join_id = str(args.get("id") or args.get("join_id") or "").strip()
        endpoint = None
        if args.get("table") and args.get("column") and args.get("ref_table") and args.get("ref_column"):
            endpoint = {
                "table": args["table"],
                "column": args["column"],
                "ref_table": args["ref_table"],
                "ref_column": args["ref_column"],
            }
        if not join_id and not endpoint:
            return ToolResult(ok=False, error=_err("delete_join", "id or full endpoint required"))
        ok = orchestrator.join_catalog.delete(
            orchestrator.instance,
            join_id=join_id,
            endpoint=endpoint,
            fingerprint=getattr(orchestrator, "connection_fingerprint", ""),
        )
        if not ok:
            return ToolResult(ok=False, error=_err("delete_join", "join not found"))
        return ToolResult(ok=True, data={"deleted": True, "id": join_id or "endpoint"})

    def _annotate_object(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        note = str(args.get("note") or "").strip()
        if not note:
            return ToolResult(ok=False, error=_err("annotate_object", "note is required"))
        table = str(args.get("table") or "").strip()
        column = str(args.get("column") or "").strip()
        scope = str(args.get("scope") or "").strip().lower()
        if scope not in {"database", "table", "column"}:
            scope = "column" if column else ("table" if table else "database")
        database = str(args.get("database") or orchestrator.run_state.database or "")
        store = getattr(orchestrator, "annotations", None)
        if store is None:
            return ToolResult(ok=False, error=_err("annotate_object", "annotation store unavailable"))
        try:
            record = store.add(
                orchestrator.instance,
                scope=scope,
                note=note,
                database=database,
                table=table,
                column=column,
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=_err("annotate_object", str(exc)))
        return ToolResult(ok=True, data={"annotation": record, "saved": True})

    registry.register(RETRIEVE_JOIN_CONTEXT, _retrieve_join_context)
    registry.register(VALIDATE_JOINS, _validate_joins)
    registry.register(LIST_JOINS, _list_joins)
    registry.register(ADD_JOIN, _add_join)
    registry.register(UPDATE_JOIN, _update_join)
    registry.register(DELETE_JOIN, _delete_join)
    registry.register(ANNOTATE_OBJECT, _annotate_object)
