from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger("dbaide.desktop.service")

from dbaide.adapters import build_adapter
from dbaide.assets import AssetBuilder, AssetSearch, AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.assets.summarizer import (
    render_database_markdown,
    render_instance_markdown,
    render_table_markdown,
)
from dbaide.config import ConfigManager
from dbaide.core import ExecutionPolicy, WorkflowRequest
from dbaide.core.workflow import WorkflowEngine
from dbaide.history.store import WorkflowHistoryStore
from dbaide.history.session_store import ChatSessionStore, make_turn
from dbaide.llm import LLMMessage, NullLLMClient, build_llm_client
from dbaide.models import ConnectionConfig, ModelConfig
from dbaide.session import Session
from dbaide.tools import QueryTools


def _to_dict(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_to_dict(item) for item in obj]
    if isinstance(obj, tuple):
        return [_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {str(key): _to_dict(value) for key, value in obj.items()}
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__slots__"):
        return {slot: _to_dict(getattr(obj, slot)) for slot in obj.__slots__ if hasattr(obj, slot)}
    if hasattr(obj, "__dict__"):
        return {key: _to_dict(value) for key, value in obj.__dict__.items()}
    return str(obj)


def _conn_payload(conn: ConnectionConfig, *, has_assets: bool) -> dict[str, Any]:
    target = conn.path if conn.type == "sqlite" else f"{conn.host}:{conn.port or ''}/{conn.database}"
    return {
        "name": conn.name,
        "type": conn.type,
        "database": conn.database,
        "host": conn.host,
        "port": conn.port,
        "user": conn.user,
        "password_env": conn.password_env,
        "path": conn.path,
        "target": target,
        "load_profile": getattr(conn, "load_profile", "production"),
        "asset_status": "ready" if has_assets else "missing",
    }


def _model_payload(model: ModelConfig) -> dict[str, Any]:
    return {
        "name": model.name,
        "provider": model.provider,
        "base_url": model.base_url,
        "api_key_env": model.api_key_env,
        "has_api_key": bool(model.api_key or model.api_key_env),
        "model": model.model,
        "timeout_seconds": model.timeout_seconds,
    }


def _validate_model_config(model: ModelConfig) -> None:
    if model.provider in {"none", ""}:
        return
    missing: list[str] = []
    if not model.base_url.strip():
        missing.append("Base URL")
    if not model.model.strip():
        missing.append("Model ID")
    if not model.api_key.strip() and not model.api_key_env.strip():
        missing.append("API Key")
    if missing:
        raise ValueError(
            "Model configuration incomplete. Missing: "
            + ", ".join(missing)
            + ". All three are required for openai_compatible."
        )


class DesktopService:
    """Facade used by the desktop UI and tests.

    The service intentionally exposes structured payloads instead of widgets so
    the GUI remains a rendering layer over the same core capabilities as CLI.
    """

    def __init__(self, cfg: ConfigManager | None = None, store: AssetStore | None = None) -> None:
        self.cfg = cfg or ConfigManager()
        self.store = store or AssetStore()
        self.join_catalog = JoinCatalogStore()
        self.history = WorkflowHistoryStore()
        self.sessions = ChatSessionStore()
        import threading
        self._build_lock = threading.Lock()
        self._active_builds: set[str] = set()

    # ── Mutual exclusion: don't query an instance while it is being built ────

    def _build_active(self, instance: str) -> bool:
        with self._build_lock:
            return instance in self._active_builds

    def _begin_build(self, instance: str) -> None:
        with self._build_lock:
            self._active_builds.add(instance)

    def _end_build(self, instance: str) -> None:
        with self._build_lock:
            self._active_builds.discard(instance)

    def _guard_busy(self, instance: str) -> None:
        if self._build_active(instance):
            raise RuntimeError(
                f"Asset build in progress for '{instance}'. Please wait for it to finish before querying."
            )

    def dispatch(self, action: str, payload: dict[str, Any] | None = None) -> Any:
        payload = payload or {}
        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "bootstrap": self.bootstrap,
            "build_assets": self.build_assets,
            "list_databases": self.list_databases,
            "schema_tree": self.schema_tree,
            "search_assets": self.search_assets,
            "read_asset": self.read_asset,
            "save_connection": self.save_connection,
            "delete_connection": self.delete_connection,
            "save_model": self.save_model,
            "delete_model": self.delete_model,
            "set_default_model": self.set_default_model,
            "ask": self.ask,
            "test_connection": self.test_connection,
            "validate_sql": self.validate_sql,
            "execute_sql": self.execute_sql,
            "browse_table": self.browse_table,
            "count_table": self.count_table,
            "table_ddl": self.table_ddl,
            "explain_sql": self.explain_sql,
            "list_history": self.list_history,
            "load_history": self.load_history,
            "delete_history": self.delete_history,
            "list_sessions": self.list_sessions,
            "load_session": self.load_session,
            "create_session": self.create_session,
            "rename_session": self.rename_session,
            "delete_session": self.delete_session,
            "asset_markdown": self.asset_markdown,
            "preview_asset": self.asset_markdown,
            "test_model": self.test_model,
            "test_model_profile": self.test_model_profile,
            "list_joins": self.list_joins,
            "add_join": self.add_join,
            "update_join": self.update_join,
            "delete_join": self.delete_join,
            "resource_defaults": self.resource_defaults,
            "save_resource_defaults": self.save_resource_defaults,
            "recent_queries": self.recent_queries,
        }
        if action not in handlers:
            raise ValueError(f"Unknown desktop action: {action}")
        return handlers[action](payload)

    def bootstrap(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        conns = self.cfg.connections()
        default = str(self.cfg._data.get("default_connection") or "")
        default_model = str(self.cfg._data.get("default_model") or "") or next(iter(self.cfg.models()), "default")
        models_map = self.cfg.models()
        return {
            "connections": [
                {
                    **_conn_payload(conn, has_assets=bool(self.store.instance_doc(conn.name))),
                    "default": name == default,
                }
                for name, conn in conns.items()
            ],
            "default_connection": default,
            "models": [_model_payload(m) for m in models_map.values()],
            "default_model": default_model,
            "model": _model_payload(self.cfg.model()),
            "asset_root": str(self.store.base_dir),
        }

    def test_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self._connection_from_payload(payload)
        build_adapter(conn, caller="gui").test()
        return {"ok": True, "message": f"Connection OK: {conn.name}"}

    def save_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self._connection_from_payload(payload)
        self.cfg.upsert_connection(conn, make_default=bool(payload.get("make_default", False)))
        return {"connection": _conn_payload(conn, has_assets=bool(self.store.instance_doc(conn.name)))}

    def delete_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Connection name is required")
        self.cfg.delete_connection(name)
        return {"deleted": name}

    def save_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "default")
        existing = self.cfg.models().get(name)
        api_key = str(payload.get("api_key") or "")
        if not api_key and existing:
            api_key = existing.api_key
        api_key_env = str(payload.get("api_key_env") or "")
        if not api_key_env and existing:
            api_key_env = existing.api_key_env
        model = ModelConfig(
            name=name,
            provider=str(payload.get("provider") or "openai_compatible"),
            base_url=str(payload.get("base_url") or ""),
            api_key_env=api_key_env,
            api_key=api_key,
            model=str(payload.get("model") or ""),
            timeout_seconds=int(payload.get("timeout_seconds") or payload.get("timeout") or 60),
        )
        _validate_model_config(model)
        self.cfg.upsert_model(model, make_default=bool(payload.get("make_default", False)))
        return {"model": _model_payload(model)}

    def delete_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Model name is required")
        self.cfg.delete_model(name)
        return {"deleted": name}

    def set_default_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        self.cfg.set_default_model(name)
        return {"default_model": name, "model": _model_payload(self.cfg.model(name))}

    def list_databases(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("name") or payload.get("connection_name") or "") or None)
        adapter = build_adapter(conn, policy=self.cfg.policy_for(conn), caller="gui")
        adapter.test()
        live = adapter.list_databases()
        built = {
            str(entry.get("name") or "")
            for entry in self.store.database_docs(conn.name)
            if str(entry.get("name") or "")
        }
        return {
            "connection": conn.name,
            "databases": [
                {"name": name, "has_assets": name in built}
                for name in live
            ],
        }

    def build_assets(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("name") or payload.get("connection_name") or "") or None)
        progress = payload.get("progress")
        progress_cb = progress if callable(progress) else (lambda _msg: None)
        load_profile_override = str(payload.get("load_profile") or "").strip()
        if load_profile_override:
            from dbaide.db.policy import resolve_policy
            policy = resolve_policy(load_profile=load_profile_override, overrides=self.cfg.resource_defaults())
        else:
            policy = self.cfg.policy_for(conn)
        # Honour the requested build concurrency: the QueryBudget caps real DB
        # concurrency at max_inflight_queries, so a higher "workers" setting would
        # otherwise have no effect. A build is explicit and exclusive, so let it run
        # as wide as the user asked (never narrower than the policy default).
        max_workers = payload.get("max_workers")
        workers = int(max_workers) if max_workers else policy.build_max_workers
        if workers > policy.max_inflight_queries:
            policy = policy.merged_with({"max_inflight_queries": workers})
        adapter = build_adapter(conn, policy=policy, caller="build")
        self._begin_build(conn.name)
        try:
            stats = AssetBuilder(
                connection=conn,
                adapter=adapter,
                store=self.store,
                llm=self._safe_llm(),
                join_catalog=self.join_catalog,
                progress=progress_cb,
            ).build(
                databases=payload.get("databases") or None,
                profile_mode=payload.get("profile_mode") or None,
                top_k=int(payload.get("top_k") or 30),
                sample_limit=int(payload.get("sample_limit") or 50),
                timeout=int(payload.get("timeout") or 0),
                per_column_timeout=int(payload.get("per_column_timeout") or 30),
                max_workers=int(max_workers) if max_workers else None,
                dry_run=bool(payload.get("dry_run", False)),
            )
        finally:
            self._end_build(conn.name)
        return {"stats": _to_dict(stats)}

    def schema_tree(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        name = str(payload.get("name") or payload.get("connection_name") or "")
        if not name:
            name = self.cfg.get_connection(None).name
        rows: list[dict[str, Any]] = []
        for db_doc in self.store.database_docs(name):
            db_name = str(db_doc.get("name") or "")
            db_row = {
                "kind": "database",
                "name": db_name,
                "path": f"{name}.{db_name}",
                "children": [],
            }
            table_docs = list(self.store.table_docs(name, db_name))
            # Reverse FK index: which tables reference each table (incoming FKs).
            referenced_by: dict[str, list[dict[str, Any]]] = {}
            for td in table_docs:
                src = str(td.get("name") or td.get("table") or "")
                for fk in (td.get("foreign_keys") or []):
                    ref = str(fk.get("ref_table") or "")
                    if ref:
                        referenced_by.setdefault(ref, []).append({
                            "table": src,
                            "column": fk.get("column") or "",
                            "ref_column": fk.get("ref_column") or "",
                        })
            for table_doc in table_docs:
                table = str(table_doc.get("name") or table_doc.get("table") or "")
                outgoing = [
                    {"column": fk.get("column") or "", "ref_table": fk.get("ref_table") or "",
                     "ref_column": fk.get("ref_column") or ""}
                    for fk in (table_doc.get("foreign_keys") or [])
                    if fk.get("ref_table")
                ]
                table_row = {
                    "kind": "table",
                    "name": table,
                    "path": f"{name}.{db_name}.{table}",
                    "column_count": table_doc.get("column_count") or len(table_doc.get("columns") or []),
                    "foreign_keys": outgoing,
                    "referenced_by": referenced_by.get(table, []),
                    "indexes": table_doc.get("indexes") or [],
                    "children": [],
                }
                for col_doc in self.store.column_docs(name, db_name, table):
                    col = str(col_doc.get("name") or col_doc.get("column") or "")
                    table_row["children"].append({
                        "kind": "column",
                        "name": col,
                        "path": f"{name}.{db_name}.{table}.{col}",
                        "data_type": col_doc.get("data_type") or col_doc.get("type") or "",
                        "primary_key": bool(col_doc.get("primary_key")),
                        "indexed": bool(col_doc.get("indexed")),
                    })
                db_row["children"].append(table_row)
            rows.append(db_row)
        return rows

    def search_assets(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        name = str(payload.get("name") or payload.get("connection_name") or "")
        query = str(payload.get("query") or "")
        limit = int(payload.get("limit") or 20)
        if not name:
            name = self.cfg.get_connection(None).name
        hits = AssetSearch(self.store).search(query, instances=[name], limit=limit)
        return [
            {
                "kind": hit.kind,
                "path": hit.path,
                "score": hit.score,
                "title": hit.title,
                "summary": hit.summary,
                "metadata": hit.metadata,
            }
            for hit in hits
        ]

    def read_asset(self, payload: dict[str, Any]) -> Any:
        path = str(payload.get("path") or "")
        parts = [part for part in path.split(".") if part]
        if len(parts) == 1:
            doc = self.store.instance_doc(parts[0])
            if doc is None:
                raise FileNotFoundError(f"Asset path not found: {path}")
            return doc
        if len(parts) == 2:
            return self.store.read_json(self.store.database_dir(parts[0], parts[1]) / "database.json")
        if len(parts) == 3:
            doc = self.store.table_doc(parts[0], parts[1], parts[2])
            if doc is None:
                raise FileNotFoundError(f"Asset path not found: {path}")
            return doc
        if len(parts) == 4:
            # No per-column docs — a column previews its parent table (the leaf).
            doc = self.store.table_doc(parts[0], parts[1], parts[2])
            if doc is None:
                raise FileNotFoundError(f"Asset path not found: {path}")
            return doc
        raise ValueError("Asset path must be instance, instance.database, instance.database.table, or instance.database.table.column")

    def ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn_name = str(payload.get("connection_name") or payload.get("name") or "")
        conn = self.cfg.get_connection(conn_name or None)
        self._guard_busy(conn.name)
        in_session_id = str(payload.get("session_id") or "")
        policy = self._policy(str(payload.get("execution_policy") or payload.get("policy") or "safe_auto"))
        database = str(payload.get("database") or "")
        request = WorkflowRequest(
            question=str(payload.get("question") or ""),
            connection_name=conn.name,
            database_scope=[database] if database else [],
            execution_policy=policy,
            limit=int(payload.get("limit") or 100),
            timeout_seconds=int(payload.get("timeout_seconds") or 10),
            show_trace=bool(payload.get("show_trace", True)),
            resume_state=payload.get("resume_state"),
            user_reply=str(payload.get("user_reply") or ""),
            schema_scope=payload.get("schema_scope") or {},
        )
        engine = WorkflowEngine(conn, self._safe_llm(), self.store, self.join_catalog)
        progress_cb = payload.get("progress")
        cancel_check = payload.get("cancel_check")
        result = engine.run(
            request,
            progress=progress_cb if callable(progress_cb) else None,
            cancel_check=cancel_check if callable(cancel_check) else None,
        )
        try:
            self.history.save(result)
        except Exception:
            pass
        payload = result.to_dict()
        payload["cli_command"] = self.cli_command(
            question=request.question,
            connection_name=conn.name,
            database=database,
            policy=policy.value,
        )
        # Group the turn into a chat session (会话). A session is created lazily on
        # the first completed turn; clarification pauses (wait_user) don't persist a
        # turn — the turn is appended once the question actually resolves.
        payload["session_id"] = self._record_session_turn(conn.name, in_session_id, request, result, database)
        return payload

    def _record_session_turn(self, conn_name, session_id, request, result, database) -> str:
        session_id = str(session_id or "")
        status = result.status.value
        if status in ("wait_user",) or result.pending_question:
            # Not a completed turn yet — just ensure a session exists to anchor it.
            if not session_id or self.sessions.load(conn_name, session_id) is None:
                session_id = self.sessions.create(conn_name)["session_id"]
            return session_id
        try:
            if not session_id or self.sessions.load(conn_name, session_id) is None:
                session_id = self.sessions.create(conn_name)["session_id"]
            self.sessions.append_turn(conn_name, session_id, make_turn(
                question=request.question,
                answer_markdown=result.answer_markdown or result.answer_plaintext or "",
                selected_sql=result.selected_sql or "",
                status=status,
                workflow_id=result.workflow_id,
                trace=[e.to_dict() for e in result.trace],
                meta={"database": database, "policy": request.execution_policy.value},
                created_at=result.created_at or None,
            ))
        except Exception:  # noqa: BLE001 — session persistence must never break a query
            logger.debug("failed to record session turn", exc_info=True)
        return session_id

    def validate_sql(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        database = str(payload.get("database") or "")
        sql = str(payload.get("sql") or "")
        tools = self._query_tools(conn)
        validation = tools.validate_sql(sql, add_limit=True)
        return {
            "ok": validation.ok,
            "normalized_sql": validation.normalized_sql,
            "issues": [{"code": i.code, "message": i.message, "severity": i.severity} for i in validation.issues],
        }

    def execute_sql(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        self._guard_busy(conn.name)
        database = str(payload.get("database") or "")
        sql = str(payload.get("sql") or "")
        limit = int(payload.get("limit") or 100)
        tools = self._query_tools(conn)
        result = tools.execute_sql(sql, database=database, limit=limit)
        return {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "sql": result.sql,
            "elapsed_ms": result.elapsed_ms,
        }

    def browse_table(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Read-only, paginated browse of a single table for the data grid.

        Builds ``SELECT * FROM <table> [WHERE …] [ORDER BY <col> <dir>] LIMIT n OFFSET m``
        with dialect-correct identifier quoting and runs it through the same guarded,
        read-only path as the SQL tab. No COUNT(*) — pagination is "has more = a full
        page came back", so big tables stay cheap."""
        from dbaide.adapters.base import quote_identifier

        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        self._guard_busy(conn.name)
        database = str(payload.get("database") or "")
        table = str(payload.get("table") or "")
        if not table:
            raise ValueError("table is required")
        page_size = max(1, min(500, int(payload.get("page_size") or 100)))
        offset = max(0, int(payload.get("offset") or 0))
        order_by = str(payload.get("order_by") or "").strip()
        order_dir = "DESC" if str(payload.get("order_dir") or "asc").lower() == "desc" else "ASC"
        where = str(payload.get("where") or "").strip()
        dialect = "mysql" if str(conn.type).lower() in ("mysql", "mariadb") else "generic"

        sql = f"SELECT * FROM {quote_identifier(table, dialect)}"
        if where:
            sql += f" WHERE {where}"
        if order_by:
            sql += f" ORDER BY {quote_identifier(order_by, dialect)} {order_dir}"
        sql += f" LIMIT {page_size} OFFSET {offset}"

        tools = self._query_tools(conn)
        result = tools.execute_sql(sql, database=database, limit=page_size)
        rows = result.rows or []
        return {
            "columns": result.columns,
            "rows": rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "sql": result.sql,
            "elapsed_ms": result.elapsed_ms,
            # Pagination echo (no COUNT(*) — "more" means a full page returned).
            "table": table, "database": database,
            "page_size": page_size, "offset": offset,
            "order_by": order_by, "order_dir": order_dir, "where": where,
            "has_more": len(rows) >= page_size,
        }

    def count_table(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Exact ``COUNT(*)`` for a table (honouring the current WHERE filter).

        Run on demand from the data grid — browsing itself never issues a COUNT so
        large tables stay cheap; the user asks for the exact total explicitly."""
        from dbaide.adapters.base import quote_identifier

        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        self._guard_busy(conn.name)
        database = str(payload.get("database") or "")
        table = str(payload.get("table") or "")
        if not table:
            raise ValueError("table is required")
        where = str(payload.get("where") or "").strip()
        dialect = "mysql" if str(conn.type).lower() in ("mysql", "mariadb") else "generic"

        sql = f"SELECT COUNT(*) AS n FROM {quote_identifier(table, dialect)}"
        if where:
            sql += f" WHERE {where}"
        tools = self._query_tools(conn)
        result = tools.execute_sql(sql, database=database, limit=1)
        count = 0
        try:
            if result.rows:
                first = result.rows[0]
                raw = next(iter(first.values())) if isinstance(first, dict) else first[0]
                count = int(raw) if raw is not None else 0
        except (StopIteration, IndexError, TypeError, ValueError):
            count = 0
        return {"count": count, "table": table, "where": where}

    def table_ddl(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The table's real CREATE TABLE DDL straight from the database — exact for
        SQLite (sqlite_master) and MySQL (SHOW CREATE TABLE), reconstructed from the
        catalog for Postgres. Falls back to a column-reconstruction if unavailable."""
        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        self._guard_busy(conn.name)
        database = str(payload.get("database") or "")
        table = str(payload.get("table") or "")
        if not table:
            raise ValueError("table is required")
        adapter = build_adapter(conn, policy=self.cfg.policy_for(conn), caller="gui")
        ddl = adapter.get_table_ddl(table, database=database)
        return {"ddl": ddl, "table": table, "database": database}

    def explain_sql(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        database = str(payload.get("database") or "")
        sql = str(payload.get("sql") or "")
        tools = self._query_tools(conn)
        result = tools.explain_sql(sql, database=database)
        return {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "sql": result.sql,
            "elapsed_ms": result.elapsed_ms,
        }

    def list_history(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        name = str(payload.get("connection_name") or payload.get("name") or "")
        if not name:
            name = self.cfg.get_connection(None).name
        return self.history.list_workflows(name, limit=int(payload.get("limit") or 50))

    def load_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = str(payload.get("connection_name") or "")
        workflow_id = str(payload.get("workflow_id") or "")
        data = self.history.load(conn, workflow_id)
        if data is None:
            raise FileNotFoundError(f"History not found: {conn}/{workflow_id}")
        return data

    def delete_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = str(payload.get("connection_name") or "") or self.cfg.get_connection(None).name
        workflow_id = str(payload.get("workflow_id") or "")
        deleted = self.history.delete(conn, workflow_id)
        return {"deleted": deleted, "workflow_id": workflow_id}

    # ── Chat sessions (会话 → 对话) ──────────────────────────────────────────--

    def _session_conn(self, payload: dict[str, Any]) -> str:
        name = str(payload.get("connection_name") or payload.get("name") or "")
        return name or self.cfg.get_connection(None).name

    def list_sessions(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return self.sessions.list_sessions(
            self._session_conn(payload), limit=int(payload.get("limit") or 100)
        )

    def load_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self._session_conn(payload)
        session_id = str(payload.get("session_id") or "")
        data = self.sessions.load(conn, session_id)
        if data is None:
            raise FileNotFoundError(f"Session not found: {conn}/{session_id}")
        return data

    def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.sessions.create(self._session_conn(payload), str(payload.get("title") or ""))

    def rename_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self._session_conn(payload)
        session_id = str(payload.get("session_id") or "")
        ok = self.sessions.rename(conn, session_id, str(payload.get("title") or ""))
        return {"renamed": ok, "session_id": session_id}

    def delete_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self._session_conn(payload)
        session_id = str(payload.get("session_id") or "")
        return {"deleted": self.sessions.delete(conn, session_id), "session_id": session_id}

    def asset_markdown(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = str(payload.get("path") or "")
        doc = self.read_asset({"path": path})
        kind = str(doc.get("kind") or "")
        if kind == "table":
            markdown = render_table_markdown(doc)
        elif kind == "database":
            markdown = render_database_markdown(doc)
        else:
            markdown = render_instance_markdown(doc)
        return {"path": path, "markdown": markdown, "doc": doc}

    def test_model(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        name = str(payload.get("name") or "").strip()
        model = self.cfg.model(name or None)
        llm = build_llm_client(model)
        if isinstance(llm, NullLLMClient):
            return {"ok": False, "message": "No model configured"}
        text = llm.complete_text([LLMMessage("user", "Reply with OK only.")])
        return {"ok": True, "message": text.strip()[:120]}

    def test_model_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Test the form values as-is without persisting them — pressing "Test"
        # must not silently save unsaved edits (or a typo'd api key).
        name = str(payload.get("name") or "default")
        existing = self.cfg.models().get(name)
        api_key = str(payload.get("api_key") or "") or (existing.api_key if existing else "")
        api_key_env = str(payload.get("api_key_env") or "") or (existing.api_key_env if existing else "")
        model = ModelConfig(
            name=name,
            provider=str(payload.get("provider") or "openai_compatible"),
            base_url=str(payload.get("base_url") or ""),
            api_key_env=api_key_env,
            api_key=api_key,
            model=str(payload.get("model") or ""),
            timeout_seconds=int(payload.get("timeout_seconds") or payload.get("timeout") or 60),
        )
        _validate_model_config(model)
        llm = build_llm_client(model)
        if isinstance(llm, NullLLMClient):
            return {"ok": False, "message": "No model configured"}
        text = llm.complete_text([LLMMessage("user", "Reply with OK only.")])
        return {"ok": True, "message": text.strip()[:120]}

    def cli_command(
        self,
        *,
        question: str,
        connection_name: str,
        database: str = "",
        policy: str = "safe_auto",
    ) -> str:
        parts = ["dbaide ask", f'"{question.replace(chr(34), chr(92)+chr(34))}"', f"--connection {connection_name}"]
        if database:
            parts.append(f"--database {database}")
        if policy and policy != "safe_auto":
            parts.append(f"--policy {policy}")
        parts.append("--show-trace")
        return " ".join(parts)

    def _query_tools(self, conn: ConnectionConfig) -> QueryTools:
        adapter = build_adapter(conn, policy=self.cfg.policy_for(conn), caller="gui")
        session = Session(conn)
        return QueryTools(adapter, session.disclosure, instance=conn.name)

    # ── Resource defaults (user-configurable numeric limits) ─────────────────

    def resource_defaults(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from dbaide.db.policy import LOAD_PROFILES
        from dataclasses import asdict
        return {
            "values": self.cfg.resource_defaults(),
            "presets": {name: asdict(p) for name, p in LOAD_PROFILES.items()},
        }

    def save_resource_defaults(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
        # Coerce known numeric keys; ignore unknowns.
        from dbaide.db.policy import ResourcePolicy
        from dataclasses import fields
        numeric_keys = {f.name for f in fields(ResourcePolicy)} - {"build_profile_mode"}
        clean: dict[str, Any] = {}
        for key, val in (values or {}).items():
            if val in (None, ""):
                continue
            if key in numeric_keys:
                try:
                    clean[key] = int(val)
                except (TypeError, ValueError):
                    continue
            elif key == "build_profile_mode":
                clean[key] = str(val)
        self.cfg.set_resource_defaults(clean)
        return {"values": self.cfg.resource_defaults()}

    # ── Query audit log (full SQL visibility) ────────────────────────────────

    def recent_queries(self, payload: dict[str, Any]) -> dict[str, Any]:
        from dbaide.observability import query_log
        name = str(payload.get("connection_name") or payload.get("name") or "")
        if not name:
            name = self.cfg.get_connection(None).name
        limit = int(payload.get("limit") or 200)
        log = query_log.for_instance(name)
        entries = [e.to_dict() for e in log.recent(limit)]
        if not entries:
            entries = log.tail_file(limit=limit)
        return {"connection": name, "queries": entries, "summary": log.summary()}

    def _connection_from_payload(self, payload: dict[str, Any]) -> ConnectionConfig:
        port = payload.get("port")
        if isinstance(port, str) and port.strip():
            port = int(port)
        elif port in ("", None):
            port = None
        return ConnectionConfig(
            name=str(payload.get("name") or "").strip(),
            type=str(payload.get("type") or "sqlite").strip(),
            database=str(payload.get("database") or "").strip(),
            host=str(payload.get("host") or "").strip(),
            port=port,
            user=str(payload.get("user") or "").strip(),
            password_env=str(payload.get("password_env") or "").strip(),
            password=str(payload.get("password") or ""),
            path=str(payload.get("path") or "").strip(),
            load_profile=str(payload.get("load_profile") or "production").strip(),
        )

    def list_joins(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        tables = payload.get("tables")
        table_list = [str(t) for t in tables] if isinstance(tables, list) else None
        joins = self.join_catalog.list_records(
            conn.name,
            database=str(payload.get("database") or ""),
            tables=table_list,
            min_confidence=float(payload.get("min_confidence") or 0.0),
            endpoint=payload if payload.get("table") and payload.get("column") else None,
        )
        return {"joins": joins, "count": len(joins)}

    def add_join(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        source = str(payload.get("source") or "user")
        record = self.join_catalog.add(
            conn.name,
            payload,
            source=source,
            database=str(payload.get("database") or conn.database or ""),
        )
        return {"join": record}

    def update_join(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        join_id = str(payload.get("id") or payload.get("join_id") or "")
        updated = self.join_catalog.update(conn.name, join_id, payload)
        if updated is None:
            raise ValueError(f"Join not found: {join_id}")
        return {"join": updated}

    def delete_join(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        join_id = str(payload.get("id") or payload.get("join_id") or "")
        endpoint = None
        if payload.get("table") and payload.get("column") and payload.get("ref_table") and payload.get("ref_column"):
            endpoint = {
                "table": payload["table"],
                "column": payload["column"],
                "ref_table": payload["ref_table"],
                "ref_column": payload["ref_column"],
            }
        ok = self.join_catalog.delete(conn.name, join_id=join_id, endpoint=endpoint)
        if not ok:
            raise ValueError("Join not found")
        return {"deleted": True}

    def _safe_llm(self):
        return build_llm_client(self.cfg.model())

    def _policy(self, value: str) -> ExecutionPolicy:
        normalized = value.replace("-", "_").lower()
        for item in ExecutionPolicy:
            if item.value == normalized:
                return item
        return ExecutionPolicy.SAFE_AUTO

    def pretty_json(self, payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
