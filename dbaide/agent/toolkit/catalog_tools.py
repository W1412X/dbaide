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
from dbaide.agent.toolkit.support import (
    _err, _normalize_tool_table, _relations_payload, _string_list, _targets_from_relations,
)


def register(registry: ToolRegistry, orchestrator) -> None:
    def _db_default(args: dict[str, Any]) -> str:
        return str(args.get("database") or orchestrator.run_state.table_database or orchestrator.run_state.database or "")

    def _normalize_endpoint(args: dict[str, Any]) -> tuple[str, dict[str, str]]:
        db_default = _db_default(args)
        left_db, table = _normalize_tool_table(orchestrator, str(args.get("table") or args.get("left_table") or ""), db_default)
        right_db, ref_table = _normalize_tool_table(orchestrator, str(args.get("ref_table") or args.get("right_table") or ""), db_default)
        database = left_db or right_db or db_default
        return database, {
            "table": table,
            "column": str(args.get("column") or args.get("left_column") or "").strip(),
            "ref_table": ref_table,
            "ref_column": str(args.get("ref_column") or args.get("right_column") or "").strip(),
        }

    def _retrieve_join_context(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        from dbaide.agent.join_evidence import JoinEvidenceRetriever

        request = str(args.get("request") or orchestrator.run_state.question or "").strip()
        tables = _string_list(args.get("tables"))
        database = _db_default(args)
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
        database = _db_default(args)
        tables_arg = args.get("tables")
        tables: list[str] | None = None
        table_items = _string_list(tables_arg)
        if table_items:
            normalized: list[tuple[str, str]] = [_normalize_tool_table(orchestrator, str(t), database) for t in table_items]
            explicit_dbs = {db for db, _table in normalized if db}
            if len(explicit_dbs) == 1:
                database = next(iter(explicit_dbs))
            tables = [table for _db, table in normalized if table]
        min_conf = float(args.get("min_confidence") or 0.0)
        endpoint = None
        if args.get("table") and args.get("column"):
            ep_db, endpoint = _normalize_endpoint(args)
            database = ep_db or database
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
        database, endpoint = _normalize_endpoint(args)
        source = str(args.get("source") or "user").strip().lower()
        if source not in {"user", "agent"}:
            source = "user"
        rel = {
            "table": endpoint["table"],
            "column": endpoint["column"],
            "ref_table": endpoint["ref_table"],
            "ref_column": endpoint["ref_column"],
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
        endpoint_keys = {"database", "table", "column", "ref_table", "ref_column"}
        fields = {
            key: value
            for key, value in args.items()
            if key not in endpoint_keys or str(value or "").strip()
        }
        if args.get("table") or args.get("ref_table"):
            ep_db, endpoint = _normalize_endpoint(args)
            if ep_db:
                fields["database"] = ep_db
            fields.update({key: value for key, value in endpoint.items() if value})
        updated = orchestrator.join_catalog.update(
            orchestrator.instance,
            join_id,
            fields,
            fingerprint=getattr(orchestrator, "connection_fingerprint", ""),
        )
        if updated is None:
            return ToolResult(ok=False, error=_err("update_join", f"join not found: {join_id}"))
        return ToolResult(ok=True, data={"join": updated, "relation": catalog_record_to_relation(updated)})

    def _delete_join(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        join_id = str(args.get("id") or args.get("join_id") or "").strip()
        endpoint = None
        if args.get("table") and args.get("column") and args.get("ref_table") and args.get("ref_column"):
            db, endpoint = _normalize_endpoint(args)
            if db:
                endpoint["database"] = db
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
        database = _db_default(args)
        database, table = _normalize_tool_table(orchestrator, str(args.get("table") or "").strip(), database)
        column = str(args.get("column") or "").strip()
        scope = str(args.get("scope") or "").strip().lower()
        if scope not in {"database", "table", "column"}:
            scope = "column" if column else ("table" if table else "database")
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
