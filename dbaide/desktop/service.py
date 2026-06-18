from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("dbaide.desktop.service")


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _positive_int(value: object, default: int, *, name: str, maximum: int | None = None) -> int:
    raw = default if value in (None, "") else value
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    if maximum is not None:
        parsed = min(parsed, int(maximum))
    return parsed

from dbaide.adapters import build_adapter
from dbaide.assets import AssetBuilder, AssetSearch, AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.annotations import AnnotationStore
from dbaide.assets.summarizer import (
    render_database_markdown,
    render_instance_markdown,
    render_table_markdown,
)
from dbaide.config import ConfigManager
from dbaide.connection_identity import connection_fingerprint
from dbaide.core import WorkflowRequest
from dbaide.core.workflow import WorkflowEngine
from dbaide.db.identifiers import normalize_db_table_for_dialect
from dbaide.desktop.service_actions import build_action_handlers
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
        "has_password": bool(conn.password or conn.password_env),
        "path": conn.path,
        "target": target,
        "load_profile": getattr(conn, "load_profile", "production"),
        "session_timezone": getattr(conn, "session_timezone", "UTC"),
        "sslmode": getattr(conn, "sslmode", ""),
        "ssl_ca": getattr(conn, "ssl_ca", ""),
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
        "context_length": model.context_length,
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
        self.annotations = AnnotationStore()
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
        handlers = build_action_handlers(self)
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
                    **_conn_payload(conn, has_assets=bool(self.store.instance_doc(conn.name, connection=conn))),
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
        payload = dict(payload or {})
        name = str(payload.get("name") or "").strip()
        existing = self.cfg.connections().get(name) if name else None
        if existing is not None:
            if not str(payload.get("password") or ""):
                payload["password"] = existing.password
            if not str(payload.get("password_env") or "").strip():
                payload["password_env"] = existing.password_env
        conn = self._connection_from_payload(payload)
        self.cfg.upsert_connection(conn, make_default=bool(payload.get("make_default", False)))
        return {"connection": _conn_payload(conn, has_assets=bool(self.store.instance_doc(conn.name, connection=conn)))}

    def delete_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Connection name is required")
        self.cfg.delete_connection(name)
        # Remove ALL per-connection data, not just the config entry — otherwise the
        # offline assets, saved joins, user notes, chat sessions, workflow history and
        # the query-audit log linger as orphans (a "fake delete"). Each store owns its
        # own layout via purge_instance(); best-effort so one failure can't block the rest.
        purged: list[str] = []
        from dbaide.observability import query_log
        purgers = (
            ("assets", lambda: self.store.purge_instance(name)),
            ("joins", lambda: self.join_catalog.purge_instance(name)),
            ("annotations", lambda: self.annotations.purge_instance(name)),
            ("sessions", lambda: self.sessions.purge_instance(name)),
            ("history", lambda: self.history.purge_instance(name)),
            ("query_log", lambda: query_log.purge_instance(name)),
        )
        for label, fn in purgers:
            try:
                if fn():
                    purged.append(label)
            except Exception as exc:  # noqa: BLE001 — never let cleanup block the delete
                logger.warning("delete_connection purge %s failed: %s", label, exc)
        return {"deleted": name, "purged": purged}

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
            context_length=payload.get("context_length") or 32000,
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
            for entry in self.store.database_docs(conn.name, connection=conn)
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

    def project_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Build the BASE (catalog-only) document from the live catalog — table/column
        structure, types, keys, native comments, indexes, FKs, DDL. NO LLM summaries,
        NO data sampling, NO profiling: it's cheap and deterministic, derivable purely
        from the connection. This is the layer the schema tree renders; the optional
        `build_assets` enrichment (semantics, samples, profiles) layers on top later.
        ``databases`` (optional list) projects only those; otherwise all visible ones."""
        conn = self.cfg.get_connection(str(payload.get("name") or payload.get("connection_name") or "") or None)
        progress = payload.get("progress")
        progress_cb = progress if callable(progress) else (lambda _msg: None)
        policy = self.cfg.policy_for(conn)
        adapter = build_adapter(conn, policy=policy, caller="build")
        self._begin_build(conn.name)
        try:
            stats = AssetBuilder(
                connection=conn,
                adapter=adapter,
                store=self.store,
                llm=None,  # base layer is catalog-only — no LLM summaries
                join_catalog=self.join_catalog,
                progress=progress_cb,
            ).build(
                databases=payload.get("databases") or None,
                sample=False,
                collect_row_counts=False,
                profile_mode="none",
                max_workers=policy.max_inflight_queries,
            )
        finally:
            self._end_build(conn.name)
        return {"stats": _to_dict(stats)}

    def _project_table_doc(self, summarizer, adapter, instance: str, database: str, table_info) -> dict[str, Any]:
        """Build ONE table's base doc (catalog-only) in memory — no write, no LLM, no
        sampling. Used by refresh to compute the live snapshot and write base docs."""
        table_key = self._table_key(adapter, table_info)
        doc_table = self._doc_table_info(adapter, table_info)
        cols = adapter.describe_table(table_key, database=database)
        fks = adapter.foreign_keys(table_key, database=database)
        try:
            idxs = adapter.indexes(table_key, database=database)
        except Exception:  # noqa: BLE001 — indexes are best-effort
            idxs = []
        try:
            ddl = adapter.get_table_ddl(table_key, database=database)
        except Exception:  # noqa: BLE001
            ddl = ""
        doc = summarizer.table_doc(
            instance=instance, database=database, table=doc_table,
            columns=cols, foreign_keys=fks, indexes=idxs, ddl=ddl, sample_rows=[],
        )
        doc["column_count"] = len(cols)  # denormalized count the tree/rollup read
        doc.update(self.store.connection_metadata(adapter.config))
        return doc

    @staticmethod
    def _table_key(adapter, table_info) -> str:
        if getattr(adapter, "dialect", "") == "postgres" and getattr(table_info, "schema", ""):
            return table_info.ref
        return table_info.name

    @staticmethod
    def _doc_table_info(adapter, table_info):
        key = DesktopService._table_key(adapter, table_info)
        if key == table_info.name:
            return table_info
        from dbaide.models import TableInfo
        return TableInfo(
            name=key,
            schema=table_info.schema,
            comment=table_info.comment,
            estimated_rows=table_info.estimated_rows,
            table_type=table_info.table_type,
        )

    # Structural fields a refresh overwrites from the live catalog; everything else
    # in a table doc (description, sample_rows, profiles, …) is enrichment and kept.
    _STRUCT_FIELDS = ("columns", "column_count", "indexes", "foreign_keys",
                      "source_comment", "ddl", "table_type", "row_count", "row_count_exact")

    def refresh_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Re-sync the base layer with the live catalog and react to changes.

        - new table/db → write its base doc;
        - dropped table/db → delete its docs AND cascade-delete its user notes;
        - structural change → update base fields, KEEP enrichment but mark it stale,
          and cascade-delete notes of any dropped columns;
        - unreachable database / un-describable table → left untouched (never deleted
          on uncertainty). User notes for surviving objects are always preserved.
        """
        from dbaide.assets.diff import diff_catalog
        from dbaide.assets.summarizer import AssetSummarizer
        from dbaide.llm import NullLLMClient

        conn = self.cfg.get_connection(str(payload.get("name") or payload.get("connection_name") or "") or None)
        instance = conn.name
        target_db = str(payload.get("database") or "").strip()
        target_table = str(payload.get("table") or "").strip()
        only_tables = {target_table} if target_table else None
        if target_table and not target_db:
            raise ValueError("database is required when refreshing a table")
        # Never projected yet → a refresh is just the initial base projection.
        if self.store.instance_doc(instance, connection=conn) is None:
            body: dict[str, Any] = {"name": instance}
            if target_db:
                body["databases"] = [target_db]
            return self.project_instance(body)

        policy = self.cfg.policy_for(conn)
        adapter = build_adapter(conn, policy=policy, caller="build")
        summarizer = AssetSummarizer(NullLLMClient())
        self._begin_build(instance)
        try:
            live_db_names = [target_db] if target_db else list(adapter.list_databases())  # raises → refresh aborts
            store_db_names = [str(d.get("name") or "") for d in self.store.database_docs(instance, connection=conn) if d.get("name")]
            gone_dbs = [] if target_db else [d for d in store_db_names if d not in set(live_db_names)]

            new_snap, failed = self._build_live_snapshot(adapter, summarizer, instance,
                                                          live_db_names, policy,
                                                          only_tables=only_tables)

            considered = set(new_snap) | set(gone_dbs)
            old_snap: dict[str, dict[str, dict]] = {}
            for db in considered:
                if only_tables:
                    stored_doc = self.store.table_doc(instance, db, target_table, connection=conn)
                    stored = {target_table: stored_doc} if stored_doc else {}
                else:
                    stored = {str(td.get("name") or td.get("table") or ""): td
                              for td in self.store.table_docs(instance, db, connection=conn)}
                for (fdb, ft) in failed:
                    if fdb == db:
                        stored.pop(ft, None)  # exclude transiently-undescribable tables from the diff
                old_snap[db] = stored

            diff = diff_catalog(old_snap, new_snap)
            self._apply_catalog_diff(instance, diff, new_snap)

            # ── rewrite rollups for affected databases + the instance ────────────
            touched = {db for (db, _t) in diff.added_tables} | {db for (db, _t) in diff.changed_tables} \
                | {db for (db, _t) in diff.removed_tables} | set(diff.added_dbs)
            for db in touched:
                if db not in diff.removed_dbs:
                    table_names = set(self._stored_table_names(instance, db))
                    table_names.update(new_snap.get(db, {}).keys())
                    table_names.difference_update(t for d, t in diff.removed_tables if d == db)
                    self._rewrite_db_rollup(summarizer, instance, db, sorted(table_names))
            self._rewrite_instance_rollup(summarizer, instance, self._stored_database_names(instance))
            return {"instance": instance, "summary": diff.summary(),
                    "added_tables": len(diff.added_tables), "removed_tables": len(diff.removed_tables),
                    "changed_tables": len(diff.changed_tables), "removed_dbs": len(diff.removed_dbs),
                    "added_dbs": len(diff.added_dbs)}
        finally:
            self._end_build(instance)

    def _build_live_snapshot(self, adapter, summarizer, instance: str, live_db_names: list[str],
                             policy, *,
                             only_tables: set[str] | None = None) -> tuple[dict[str, dict[str, dict]], set[tuple[str, str]]]:
        """Project the current live catalog into base docs, in memory (no writes).

        Returns (snapshot, failed) where snapshot is {db: {table: base_doc}} and failed
        is the set of (db, table) that couldn't be described this run — excluded from
        the diff so a transient read error is never mistaken for a dropped table. An
        unreachable database is skipped entirely (left untouched downstream).

        Per-table metadata is projected concurrently so a sync of a many-table instance
        isn't latency-bound on sequential round-trips; the connection pool and query
        budget cap real database load at policy.max_inflight_queries. Futures resolve
        in this (caller) thread, so the snapshot dict needs no locking.
        """
        new_snap: dict[str, dict[str, dict]] = {}
        failed: set[tuple[str, str]] = set()
        work: list[tuple[str, Any]] = []
        for db in live_db_names:
            try:
                tinfos = adapter.list_tables(database=db)
            except Exception:  # noqa: BLE001 — unreachable db: leave it untouched
                continue
            new_snap[db] = {}
            if only_tables is not None:
                tinfos = [
                    ti for ti in tinfos
                    if self._table_key(adapter, ti) in only_tables or ti.name in only_tables
                ]
            work.extend((db, ti) for ti in tinfos)
        if work:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            workers = max(1, int(getattr(policy, "max_inflight_queries", 1)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(self._project_table_doc, summarizer, adapter, instance, db, ti):
                        (db, ti) for (db, ti) in work}
                for fut in as_completed(futs):
                    db, ti = futs[fut]
                    try:
                        new_snap[db][self._table_key(adapter, ti)] = fut.result()
                    except Exception:  # noqa: BLE001 — can't describe now: don't treat as dropped
                        failed.add((db, self._table_key(adapter, ti)))
        return new_snap, failed

    def _apply_catalog_diff(self, instance: str, diff, new_snap: dict[str, dict[str, dict]]) -> None:
        """Apply a computed catalog diff to the base layer: delete docs for gone
        objects (cascading their user notes), write base docs for new tables, and for
        changed tables overwrite the structural fields + re-fingerprint while KEEPING
        any enrichment (flagged stale when structure moved under it)."""
        from dbaide.assets.diff import table_fingerprint
        for db in diff.removed_dbs:
            self.store.delete_database(instance, db)
            self.annotations.delete_under(instance, database=db)
        for db in diff.added_dbs:
            for table, doc in new_snap.get(db, {}).items():
                self.store.write_json(self.store.table_dir(instance, db, table) / "table.json", doc)
        for (db, table) in diff.removed_tables:
            self.store.delete_table(instance, db, table)
            self.annotations.delete_under(instance, database=db, table=table)
        for (db, table, col) in diff.removed_columns:
            self.annotations.delete_under(instance, database=db, table=table, column=col)
        for (db, table) in diff.added_tables:
            self.store.write_json(self.store.table_dir(instance, db, table) / "table.json",
                                  new_snap[db][table])
        for (db, table) in diff.changed_tables:
            stored = self.store.table_doc(instance, db, table) or {}
            base = new_snap[db][table]
            for k in self._STRUCT_FIELDS:
                stored[k] = base.get(k)
            stored["base_fingerprint"] = table_fingerprint(base)
            if stored.get("sample_rows") or stored.get("enriched_at"):
                stored["enrichment_stale"] = True  # structure moved under the enrichment
            self.store.write_json(self.store.table_dir(instance, db, table) / "table.json", stored)

    def _rewrite_db_rollup(self, summarizer, instance: str, database: str, table_names: list[str]) -> None:
        conn = self.cfg.get_connection(instance)
        docs = [
            d
            for d in (self.store.table_doc(instance, database, t, connection=conn) for t in table_names)
            if d
        ]
        self.store.write_json(self.store.database_dir(instance, database) / "tables.json",
                              {
                                  "instance": instance,
                                  "database": database,
                                  **self.store.connection_metadata(conn),
                                  "tables": docs,
                              })
        ddoc = summarizer.database_doc(instance=instance, database=database, tables=docs)
        ddoc["table_count"] = len(docs)
        ddoc.update(self.store.connection_metadata(conn))
        self.store.write_json(self.store.database_dir(instance, database) / "database.json", ddoc)

    def _rewrite_instance_rollup(self, summarizer, instance: str, db_names: list[str]) -> None:
        conn = self.cfg.get_connection(instance)
        db_docs: list[dict[str, Any]] = []
        for db in db_names:
            d = self.store._read_optional(self.store.database_dir(instance, db) / "database.json")
            if isinstance(d, dict):
                db_docs.append(d)
        self.store.write_json(self.store.instance_dir(instance) / "databases.json", {
            "instance": instance,
            **self.store.connection_metadata(conn),
            "databases": [{"name": d.get("name"), "description": d.get("description"),
                           "table_count": d.get("table_count")} for d in db_docs],
        })
        idoc = summarizer.instance_doc(instance=instance, databases=db_docs)
        existing = self.store.instance_doc(instance, connection=conn) or {}
        idoc["built_at"] = existing.get("built_at")
        conn_type = existing.get("connection_type")
        if not conn_type:
            conn_type = conn.type
        idoc["connection_type"] = conn_type
        idoc.update(self.store.connection_metadata(conn))
        self.store.write_json(self.store.instance_dir(instance) / "instance.json", idoc)

    def _stored_database_names(self, instance: str) -> list[str]:
        conn = self.cfg.get_connection(instance)
        return sorted(str(d.get("name") or "") for d in self.store.database_docs(instance, connection=conn) if d.get("name"))

    def _stored_table_names(self, instance: str, database: str) -> list[str]:
        conn = self.cfg.get_connection(instance)
        return sorted(
            str(t.get("name") or t.get("table") or "")
            for t in self.store.table_docs(instance, database, connection=conn)
            if t.get("name") or t.get("table")
        )

    def enrich_table(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Enrich ONE table's document — LLM summary + sample rows + profiling — from
        the live database, leaving every other table's doc untouched. The optional,
        table-granular counterpart to the catalog-only base layer (project_instance)."""
        conn = self.cfg.get_connection(str(payload.get("name") or payload.get("connection_name") or "") or None)
        database = str(payload.get("database") or "").strip()
        table = str(payload.get("table") or "").strip()
        if not database or not table:
            raise ValueError("database and table are required")
        progress = payload.get("progress")
        progress_cb = progress if callable(progress) else (lambda _msg: None)
        policy = self.cfg.policy_for(conn)
        adapter = build_adapter(conn, policy=policy, caller="build")
        self._begin_build(conn.name)
        try:
            stats = AssetBuilder(
                connection=conn,
                adapter=adapter,
                store=self.store,
                llm=self._safe_llm(),  # enrichment uses the model when one is configured
                join_catalog=self.join_catalog,
                progress=progress_cb,
            ).build(
                databases=[database],
                tables=[table],
                profile_mode=payload.get("profile_mode") or None,
                sample_limit=int(payload.get("sample_limit") or 50),
            )
        finally:
            self._end_build(conn.name)
        return {"stats": _to_dict(stats)}

    def schema_tree(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        name = str(payload.get("name") or payload.get("connection_name") or "")
        if not name:
            name = self.cfg.get_connection(None).name
        conn = self.cfg.get_connection(name)
        rows: list[dict[str, Any]] = []
        all_table_docs: list[dict[str, Any]] = []
        for db_doc in self.store.database_docs(name, connection=conn):
            db_name = str(db_doc.get("name") or "")
            db_row = {
                "kind": "database",
                "name": db_name,
                "path": f"{name}.{db_name}",
                "children": [],
            }
            table_docs = list(self.store.table_docs(name, db_name, connection=conn))
            all_table_docs.extend(table_docs)
            referenced_by = self._referenced_by_index(table_docs)
            for table_doc in table_docs:
                db_row["children"].append(
                    self._table_tree_row(name, db_name, table_doc, referenced_by))
            rows.append(db_row)
        summary = self._schema_asset_summary(name, conn, all_table_docs)
        for row in rows:
            row["asset_summary"] = summary
        return rows

    def _schema_asset_summary(self, instance: str, conn: ConnectionConfig, table_docs: list[dict[str, Any]]) -> dict[str, Any]:
        instance_doc = self.store.instance_doc(instance, connection=conn) or {}
        stats = instance_doc.get("stats") or {}
        errors = stats.get("errors") if isinstance(stats, dict) else []
        if not isinstance(errors, list):
            errors = []
        total_tables = len(table_docs)
        total_columns = sum(int(td.get("column_count") or len(td.get("columns") or [])) for td in table_docs)
        sampled_tables = sum(1 for td in table_docs if td.get("sample_rows") or td.get("enriched_at"))
        stale_tables = sum(1 for td in table_docs if td.get("enrichment_stale"))
        if not total_tables:
            state = "failed" if errors else "missing"
        elif errors:
            state = "failed"
        elif stale_tables:
            state = "stale"
        elif sampled_tables == total_tables:
            state = "sampled"
        elif sampled_tables:
            state = "partial"
        else:
            state = "base"
        return {
            "state": state,
            "tables": total_tables,
            "columns": total_columns,
            "sampled_tables": sampled_tables,
            "stale_tables": stale_tables,
            "errors": len(errors),
            "profile_state": "on_demand",
            "completed_at": instance_doc.get("completed_at"),
        }

    @staticmethod
    def _referenced_by_index(table_docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Reverse foreign-key index: ref_table → list of incoming FKs (which tables
        reference it), so each table node can show what points AT it."""
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
        return referenced_by

    def _table_tree_row(self, instance: str, db_name: str, table_doc: dict[str, Any],
                        referenced_by: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        """One table node for the schema tree: outgoing/incoming FKs, denormalized
        column count, enrichment status, and its column children."""
        table = str(table_doc.get("name") or table_doc.get("table") or "")
        outgoing = [
            {"column": fk.get("column") or "", "ref_table": fk.get("ref_table") or "",
             "ref_column": fk.get("ref_column") or ""}
            for fk in (table_doc.get("foreign_keys") or [])
            if fk.get("ref_table")
        ]
        children = [
            {
                "kind": "column",
                "name": str(col_doc.get("name") or col_doc.get("column") or ""),
                "path": f"{instance}.{db_name}.{table}.{col_doc.get('name') or col_doc.get('column') or ''}",
                "data_type": col_doc.get("data_type") or col_doc.get("type") or "",
                "primary_key": bool(col_doc.get("primary_key")),
                "indexed": bool(col_doc.get("indexed")),
            }
                for col_doc in self.store.column_docs(
                    instance,
                    db_name,
                    table,
                    connection=self.cfg.get_connection(instance),
                )
        ]
        return {
            "kind": "table",
            "name": table,
            "path": f"{instance}.{db_name}.{table}",
            "column_count": table_doc.get("column_count") or len(table_doc.get("columns") or []),
            "foreign_keys": outgoing,
            "referenced_by": referenced_by.get(table, []),
            "indexes": table_doc.get("indexes") or [],
            # Enrichment status for the tree: base = catalog-only (structure),
            # enriched = has samples/summary, stale = structure moved under it.
            "enriched": bool(table_doc.get("sample_rows")) or bool(table_doc.get("enriched_at")),
            "stale": bool(table_doc.get("enrichment_stale")),
            "asset_state": (
                "stale" if table_doc.get("enrichment_stale")
                else "sampled" if (table_doc.get("sample_rows") or table_doc.get("enriched_at"))
                else "base"
            ),
            "profile_state": "on_demand",
            "children": children,
        }

    def search_assets(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        name = str(payload.get("name") or payload.get("connection_name") or "")
        query = str(payload.get("query") or "")
        limit = int(payload.get("limit") or 20)
        if not name:
            name = self.cfg.get_connection(None).name
        conn = self.cfg.get_connection(name)
        hits = AssetSearch(self.store).search(
            query,
            instances=[name],
            limit=limit,
            fingerprint=connection_fingerprint(conn),
        )
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
        if not 1 <= len(parts):
            raise ValueError("Asset path must be instance, instance.database, instance.database.table, or instance.database.table.column")
        try:
            conn = self.cfg.get_connection(parts[0]) if parts else None
        except KeyError as exc:
            raise ValueError(
                "Asset path must be instance, instance.database, instance.database.table, or instance.database.table.column"
            ) from exc
        if len(parts) == 1:
            doc = self.store.instance_doc(parts[0], connection=conn)
            if doc is None:
                raise FileNotFoundError(f"Asset path not found: {path}")
            return doc
        if len(parts) == 2:
            if not self.store.connection_matches(parts[0], connection=conn):
                raise FileNotFoundError(f"Asset path not found: {path}")
            return self.store.read_json(self.store.database_dir(parts[0], parts[1]) / "database.json")
        if len(parts) >= 3:
            table = ".".join(parts[2:])
            doc = self.store.table_doc(parts[0], parts[1], table, connection=conn)
            if doc is None:
                # No per-column docs — a column previews its parent table (the leaf).
                # For schema-qualified Postgres tables, the table itself may contain
                # dots, so the column is only the final path part.
                table = ".".join(parts[2:-1])
                doc = self.store.table_doc(parts[0], parts[1], table, connection=conn)
            if doc is None:
                raise FileNotFoundError(f"Asset path not found: {path}")
            return doc
        raise ValueError("Asset path must be instance, instance.database, instance.database.table, or instance.database.table.column")

    def ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn_name = str(payload.get("connection_name") or payload.get("name") or "")
        conn = self.cfg.get_connection(conn_name or None)
        self._guard_busy(conn.name)
        in_session_id = str(payload.get("session_id") or "")
        database = str(payload.get("database") or "")
        # Session memory: load every completed turn already in this chat session
        # so the agent gets [Prior turns] context and L2 carry-over criteria.
        # On resume (ask_user pause continuing), we still load the TURNS so that
        # retrieve_turn / list_earlier_turns tools work and the [Prior turns]
        # prompt section stays consistent — but skip active_criteria because
        # those were already seeded into clarifications before the pause and
        # are captured in the loop-state snapshot (re-seeding would double-count).
        is_resume = bool(payload.get("resume_state") or payload.get("user_reply"))
        session_turns, active_criteria, session_messages = self._load_session_memory(
            conn.name, in_session_id,
            skip_criteria=is_resume,
        )
        if session_messages is None:
            session_messages = []
        # Carry forward: if the user didn't attach new schema scope on this turn,
        # inherit the most recent prior turn's scope so the pinned context sticks
        # across follow-up questions in the same session.
        if not payload.get("schema_scope") and session_turns:
            for prior in reversed(session_turns):
                prior_scope = prior.get("schema_scope")
                if prior_scope and (prior_scope.get("databases") or prior_scope.get("tables")):
                    payload.setdefault("schema_scope", prior_scope)
                    break
        request = self._build_request(
            payload, connection_name=conn.name, database=database,
            session_turns=session_turns, active_criteria=active_criteria,
            session_messages=session_messages,
        )
        engine = WorkflowEngine(conn, self._safe_llm(), self.store, self.join_catalog,
                                model_config=self.cfg.model())
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
        )
        # Group the turn into a chat session (会话). A session is created lazily on
        # the first completed turn; clarification pauses (wait_user) don't persist a
        # turn — the turn is appended once the question actually resolves. The LLM
        # message stream is persisted atomically WITH the completed turn (one
        # locked write) so an interleaved second turn cannot lose-update it.
        payload["session_id"] = self._record_session_turn(conn.name, in_session_id, request, result, database)
        return payload

    def _build_request(self, payload: dict[str, Any], *, connection_name: str,
                       database: str,
                       session_turns: list[dict[str, Any]] | None = None,
                       active_criteria: list[str] | None = None,
                       session_messages: list[dict[str, str]] | None = None) -> WorkflowRequest:
        """Assemble the WorkflowRequest from a GUI ask payload (defaults applied here so
        ask() reads as request → run → record)."""
        conn = self.cfg.get_connection(connection_name)
        resource_policy = self.cfg.policy_for(conn)
        request = WorkflowRequest(
            question=str(payload.get("question") or ""),
            connection_name=connection_name,
            database_scope=[database] if database else [],
            limit=_positive_int(
                payload.get("limit"),
                resource_policy.default_row_limit,
                name="limit",
                maximum=resource_policy.max_row_limit,
            ),
            timeout_seconds=_positive_int(
                payload.get("timeout_seconds"),
                resource_policy.statement_timeout_seconds,
                name="timeout_seconds",
                maximum=600,
            ),
            resume_state=payload.get("resume_state"),
            user_reply=str(payload.get("user_reply") or ""),
            schema_scope=payload.get("schema_scope") or {},
            stream_answers=bool(payload.get("stream_answers", self.cfg.stream_answers())),
            session_turns=session_turns or [],
            active_criteria=active_criteria or [],
            session_messages=session_messages,
        )
        # Stash the raw UI attachment chips so _record_session_turn can persist
        # them alongside schema_scope — the UI restores them as visual tags when
        # reloading a session.
        request.ui_attachments = list(payload.get("attachments") or [])
        return request

    def _load_session_memory(
        self, conn_name: str, session_id: str, *, skip_criteria: bool = False,
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]] | None]:
        """Return (session_turns, active_criteria, session_messages).

        - session_turns: every COMPLETED turn in this chat session (oldest→newest),
          so the orchestrator can summarise the most-recent few into the prompt and
          back retrieve_turn / list_earlier_turns.
        - active_criteria: dedup'd union of every confirmed criterion across the
          session. The most-recent occurrence wins (later turns can refine an
          earlier statement). These are seeded into the new run's clarifications.
        - session_messages: the persisted LLM message stream for session continuity
          (None if not yet available — first turn or legacy session).
        - skip_criteria=True for resume runs: an ask_user pause is one in-flight
          turn; criteria were already seeded before the pause and live in the
          loop-state snapshot. Re-seeding would double-count. But turns are always
          loaded so the retrieve_turn / list_earlier_turns tools stay operational
          and the [Prior turns] prompt section is consistent across pause/resume.
        """
        if not session_id:
            return [], [], None
        session = self.sessions.load(conn_name, session_id)
        if not isinstance(session, dict):
            return [], [], None
        all_turns = [t for t in (session.get("turns") or []) if isinstance(t, dict)]
        completed = [t for t in all_turns if str(t.get("status") or "") == "completed"]
        # Load persisted message stream for session continuity.
        session_messages = None
        raw_msgs = session.get("messages")
        if isinstance(raw_msgs, list) and raw_msgs:
            session_messages = [
                m for m in raw_msgs
                if isinstance(m, dict) and "role" in m and "content" in m
            ]
        if skip_criteria:
            return completed, [], session_messages
        # Dedupe criteria preserving order, but later occurrences override earlier
        # (so a follow-up turn that refined "all 2024" → "Q4 2024" sticks).
        seen: dict[str, int] = {}
        for i, turn in enumerate(completed):
            for c in (turn.get("clarifications") or []):
                key = str(c).strip()
                if key:
                    seen[key] = i  # last writer wins
        criteria = [c for c, _ in sorted(seen.items(), key=lambda kv: kv[1])]
        return completed, criteria, session_messages

    def _record_session_turn(self, conn_name, session_id, request, result, database) -> str:
        session_id = str(session_id or "")
        status = result.status.value
        if status in ("wait_user",) or result.pending_question:
            # Not a completed turn yet — just ensure a session exists to anchor it.
            # We deliberately do NOT persist the partial message stream here: the
            # resume continues from the resume_state snapshot, not session.messages,
            # so leaving session.messages at the last completed turn keeps the
            # session clean if the user abandons the pause and asks a fresh question.
            if not session_id or self.sessions.load(conn_name, session_id) is None:
                session_id = self.sessions.create(conn_name)["session_id"]
            return session_id
        try:
            if not session_id or self.sessions.load(conn_name, session_id) is None:
                session_id = self.sessions.create(conn_name)["session_id"]
            # Persist clarifications + disclosed tables on the turn so the next
            # turn's session-memory load can carry them forward / show them.
            clarifications = list(getattr(result, "clarifications", []) or [])
            disclosed = list(getattr(result, "disclosed_tables", []) or [])
            # Persist the user's composer attachments + structured schema_scope so
            # (a) the UI can restore attachment tags when loading the session and
            # (b) the agent can carry forward pinned scope on follow-up turns.
            attachments = list(getattr(request, "ui_attachments", None) or [])
            schema_scope = getattr(request, "schema_scope", None) or {}
            # Persist the completed turn and the LLM message stream in one locked
            # write so a concurrent turn on the same session cannot lose-update it.
            session_messages = getattr(result, "session_messages", None)
            self.sessions.append_turn(conn_name, session_id, make_turn(
                question=request.question,
                answer_markdown=result.answer_markdown or result.answer_plaintext or "",
                selected_sql=result.selected_sql or "",
                status=status,
                workflow_id=result.workflow_id,
                trace=[e.to_dict() for e in result.trace],
                meta={"database": database},
                clarifications=clarifications,
                disclosed_tables=disclosed,
                attachments=attachments,
                schema_scope=schema_scope,
                created_at=result.created_at or None,
                charts=list(getattr(result, "charts", []) or []),
                executed_sqls=list(getattr(result, "executed_sqls", []) or []),
            ), messages=session_messages)
        except Exception:  # noqa: BLE001 — session persistence must never break a query
            logger.debug("failed to record session turn", exc_info=True)
        return session_id

    def validate_sql(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        sql = str(payload.get("sql") or "")
        policy = self.cfg.policy_for(conn)
        limit = _positive_int(payload.get("limit"), policy.default_row_limit, name="limit", maximum=policy.max_row_limit)
        tools = self._query_tools(conn)
        validation = tools.validate_sql_report(sql, add_limit=True, limit=limit)
        return {
            "ok": validation.ok,
            "normalized_sql": validation.normalized_sql,
            "issues": [{"message": i, "severity": "error"} for i in validation.issues],
            "warnings": validation.warnings,
            "risk_level": validation.risk_level,
            "requires_confirmation": validation.requires_confirmation,
        }

    def execute_sql(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        self._guard_busy(conn.name)
        database = str(payload.get("database") or "")
        sql = str(payload.get("sql") or "")
        policy = self.cfg.policy_for(conn)
        limit = _positive_int(payload.get("limit"), policy.default_row_limit, name="limit", maximum=policy.max_row_limit)
        tools = self._query_tools(conn)
        report = tools.validate_sql_report(sql, add_limit=True, limit=limit)
        if not report.ok:
            raise ValueError("; ".join(report.issues))
        confirmed_sql = str(payload.get("confirmed_sql") or "")
        if report.requires_confirmation and confirmed_sql != report.normalized_sql:
            return {
                "pending_confirmation": True,
                "normalized_sql": report.normalized_sql,
                "warnings": report.warnings,
                "risk_level": report.risk_level,
            }
        result = tools.execute_sql(
            report.normalized_sql,
            database=database,
            limit=limit,
            confirmed=report.requires_confirmation,
        )
        return {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "sql": result.sql,
            "elapsed_ms": result.elapsed_ms,
        }

    @staticmethod
    def _table_dialect(conn) -> str:
        """Identifier-quoting dialect for a connection (only MySQL/MariaDB differ)."""
        return "mysql" if str(conn.type).lower() in ("mysql", "mariadb") else "generic"

    @staticmethod
    def _select_from(table: str, where: str, dialect: str, *, columns: str = "*") -> str:
        """`SELECT <columns> FROM <quoted table> [WHERE <where>]` — the shared head of
        the data-grid browse and count queries. The table is quoted; `where` is the
        caller's raw filter, neutralized downstream by the guarded read-only executor
        (single-statement, no write keywords)."""
        from dbaide.adapters.base import quote_identifier
        sql = f"SELECT {columns} FROM {quote_identifier(table, dialect)}"
        if where:
            sql += f" WHERE {where}"
        return sql

    def browse_table(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Read-only, paginated browse of a single table for the data grid.

        Builds ``SELECT * FROM <table> [WHERE …] [ORDER BY <col> <dir>] LIMIT n OFFSET m``
        with dialect-correct identifier quoting and runs it through the same guarded,
        read-only path as the SQL tab. No COUNT(*) — pagination is "has more = a full
        page came back", so big tables stay cheap."""
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
        dialect = self._table_dialect(conn)

        sql = self._select_from(table, where, dialect)
        if order_by:
            from dbaide.adapters.base import quote_identifier
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
        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        self._guard_busy(conn.name)
        database = str(payload.get("database") or "")
        table = str(payload.get("table") or "")
        if not table:
            raise ValueError("table is required")
        where = str(payload.get("where") or "").strip()
        dialect = self._table_dialect(conn)

        sql = self._select_from(table, where, dialect, columns="COUNT(*) AS n")
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

    def export_table_all(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Export all rows (no LIMIT/OFFSET) for a table with optional WHERE/ORDER BY.

        Used by the "Export all rows" option in the data browser."""
        conn = self.cfg.get_connection(str(payload.get("connection_name") or "") or None)
        self._guard_busy(conn.name)
        database = str(payload.get("database") or "")
        table = str(payload.get("table") or "")
        if not table:
            raise ValueError("table is required")
        order_by = str(payload.get("order_by") or "").strip()
        order_dir = "DESC" if str(payload.get("order_dir") or "asc").lower() == "desc" else "ASC"
        where = str(payload.get("where") or "").strip()
        dialect = self._table_dialect(conn)

        sql = self._select_from(table, where, dialect)
        if order_by:
            from dbaide.adapters.base import quote_identifier
            sql += f" ORDER BY {quote_identifier(order_by, dialect)} {order_dir}"

        tools = self._query_tools(conn)
        result = tools.execute_sql(sql, database=database, limit=50_000)
        return {
            "columns": result.columns,
            "rows": result.rows or [],
            "row_count": result.row_count,
        }

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
        from dbaide.annotations import apply_notes_to_doc

        path = str(payload.get("path") or "")
        doc = self.read_asset({"path": path})
        instance = path.split(".")[0] if path else ""
        if instance:
            apply_notes_to_doc(self.annotations, instance, doc)  # fold in user notes
        kind = str(doc.get("kind") or "")
        if kind == "table":
            markdown = render_table_markdown(doc)
        elif kind == "database":
            markdown = render_database_markdown(doc)
        else:
            markdown = render_instance_markdown(doc)
        return {"path": path, "markdown": markdown, "doc": doc}

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
            context_length=payload.get("context_length") or 32000,
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
    ) -> str:
        parts = ["dbaide ask", f'"{question.replace(chr(34), chr(92)+chr(34))}"', f"--conn {connection_name}"]
        if database:
            parts.append(f"--database {database}")
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
        numeric_keys = ({f.name for f in fields(ResourcePolicy)} - {"build_profile_mode"}) | {"max_concurrent_runs"}
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
            session_timezone=str(payload.get("session_timezone") or "UTC").strip(),
            sslmode=str(payload.get("sslmode") or "").strip(),
            ssl_ca=str(payload.get("ssl_ca") or "").strip(),
        )

    def list_joins(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        database = str(payload.get("database") or "")
        tables = payload.get("tables")
        table_list = None
        if isinstance(tables, list):
            normalized = [self._normalize_conn_table(conn, str(t), database) for t in tables if str(t).strip()]
            explicit_dbs = {db for db, _table in normalized if db}
            if len(explicit_dbs) == 1:
                database = next(iter(explicit_dbs))
            table_list = [table for _db, table in normalized if table]
        endpoint = None
        if payload.get("table") and payload.get("column"):
            ep_db, endpoint = self._join_endpoint(payload, default_database=database)
            database = ep_db or database
        joins = self.join_catalog.list_records(
            conn.name,
            database=database,
            tables=table_list,
            min_confidence=_safe_float(payload.get("min_confidence")),
            endpoint=endpoint,
            fingerprint=connection_fingerprint(conn),
        )
        return {"joins": joins, "count": len(joins)}

    def add_join(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        source = str(payload.get("source") or "user")
        database, endpoint = self._join_endpoint(
            payload,
            default_database=str(payload.get("database") or conn.database or ""),
        )
        rel = {**payload, **endpoint}
        record = self.join_catalog.add(
            conn.name,
            rel,
            source=source,
            database=database,
            fingerprint=connection_fingerprint(conn),
        )
        return {"join": record}

    def update_join(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        join_id = str(payload.get("id") or payload.get("join_id") or "")
        fields = dict(payload)
        if payload.get("table") or payload.get("ref_table"):
            database, endpoint = self._join_endpoint(
                payload,
                default_database=str(payload.get("database") or conn.database or ""),
            )
            fields.update({key: value for key, value in endpoint.items() if value})
            if database:
                fields["database"] = database
        updated = self.join_catalog.update(
            conn.name,
            join_id,
            fields,
            fingerprint=connection_fingerprint(conn),
        )
        if updated is None:
            raise ValueError(f"Join not found: {join_id}")
        return {"join": updated}

    def delete_join(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        join_id = str(payload.get("id") or payload.get("join_id") or "")
        endpoint = None
        if payload.get("table") and payload.get("column") and payload.get("ref_table") and payload.get("ref_column"):
            database, endpoint = self._join_endpoint(
                payload,
                default_database=str(payload.get("database") or conn.database or ""),
            )
            if database:
                endpoint["database"] = database
        ok = self.join_catalog.delete(
            conn.name,
            join_id=join_id,
            endpoint=endpoint,
            fingerprint=connection_fingerprint(conn),
        )
        if not ok:
            raise ValueError("Join not found")
        return {"deleted": True}

    def _join_endpoint(self, payload: dict[str, Any], *, default_database: str = "") -> tuple[str, dict[str, str]]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        left_db, table = self._normalize_conn_table(
            conn, str(payload.get("table") or payload.get("left_table") or ""), default_database
        )
        right_db, ref_table = self._normalize_conn_table(
            conn, str(payload.get("ref_table") or payload.get("right_table") or ""), default_database
        )
        database = left_db or right_db or default_database
        return database, {
            "table": table,
            "column": str(payload.get("column") or payload.get("left_column") or "").strip(),
            "ref_table": ref_table,
            "ref_column": str(payload.get("ref_column") or payload.get("right_column") or "").strip(),
        }

    @staticmethod
    def _normalize_conn_table(conn: ConnectionConfig, table: str, database: str = "") -> tuple[str, str]:
        dialect = "mysql" if str(conn.type or "").lower() in {"mysql", "mariadb"} else str(conn.type or "").lower()
        return normalize_db_table_for_dialect(table, database, dialect)

    # ── Object annotations (user notes on db/table/column) ──────────────────

    def list_annotations(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        records = self.annotations.list_records(
            conn.name,
            scope=str(payload.get("scope") or ""),
            database=str(payload.get("database") or ""),
            table=str(payload.get("table") or ""),
            column=str(payload.get("column") or ""),
        )
        return {"annotations": records, "count": len(records)}

    def _annotation_scope(self, payload: dict[str, Any]) -> str:
        scope = str(payload.get("scope") or "").strip().lower()
        if scope in {"database", "table", "column"}:
            return scope
        if str(payload.get("column") or "").strip():
            return "column"
        if str(payload.get("table") or "").strip():
            return "table"
        return "database"

    def add_annotation(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        record = self.annotations.add(
            conn.name,
            scope=self._annotation_scope(payload),
            note=str(payload.get("note") or ""),
            database=str(payload.get("database") or ""),
            table=str(payload.get("table") or ""),
            column=str(payload.get("column") or ""),
        )
        return {"annotation": record}

    def delete_annotation(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self.cfg.get_connection(str(payload.get("connection_name") or payload.get("name") or None))
        ann_id = str(payload.get("id") or "")
        if ann_id:
            ok = self.annotations.delete(conn.name, ann_id=ann_id)
        else:
            # Object-identity delete (used when an inline note is cleared).
            ok = self.annotations.delete(
                conn.name,
                scope=self._annotation_scope(payload),
                database=str(payload.get("database") or ""),
                table=str(payload.get("table") or ""),
                column=str(payload.get("column") or ""),
            )
        # Clearing an already-empty note is a no-op, not an error.
        return {"deleted": bool(ok)}

    # ── import / export ──────────────────────────────────────────────────--

    def export_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Export one connection's config + joins + annotations as a portable dict."""
        name = str(payload.get("connection_name") or payload.get("name") or "")
        conn = self.cfg.get_connection(name)

        conn_dict: dict[str, Any] = {
            "name": conn.name,
            "type": conn.type,
            "database": conn.database,
            "host": conn.host,
            "port": conn.port,
            "user": conn.user,
            "password_env": conn.password_env,
            "password": conn.password,
            "path": conn.path,
            "load_profile": conn.load_profile,
            "session_timezone": conn.session_timezone,
            "sslmode": getattr(conn, "sslmode", ""),
            "ssl_ca": getattr(conn, "ssl_ca", ""),
        }
        # Strip empty values for cleaner output.
        conn_dict = {k: v for k, v in conn_dict.items() if v not in (None, "", 0)}
        conn_dict.setdefault("name", conn.name)
        conn_dict.setdefault("type", conn.type)

        joins = self.join_catalog._load(conn.name)
        annotations = self.annotations._load(conn.name)

        from datetime import datetime, timezone
        return {
            "dbaide_export": {
                "version": 1,
                "type": "connection",
                "exported_at": datetime.now(timezone.utc).isoformat(),
            },
            "connection": conn_dict,
            "joins": joins,
            "annotations": annotations,
        }

    def import_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Import a connection from a previously exported dict."""
        data = payload.get("data") or {}
        export_meta = data.get("dbaide_export") or {}
        if export_meta.get("type") not in ("connection", "full"):
            raise ValueError("Not a valid DBAide export file")

        if export_meta.get("type") == "full":
            return self._import_full(data)

        conn_data = data.get("connection") or {}
        name = str(conn_data.get("name") or "")
        if not name:
            raise ValueError("Export file is missing a connection name")

        from dbaide.config import _CONNECTION_KEYS
        conn_payload = {k: v for k, v in conn_data.items() if k in _CONNECTION_KEYS}
        conn_payload.setdefault("name", name)
        conn_payload.setdefault("type", "")
        conn = ConnectionConfig(**conn_payload)
        self.cfg.upsert_connection(conn, make_default=False)

        # Merge joins (user-source only; avoid duplicating agent-generated entries).
        imported_joins = data.get("joins") or []
        if imported_joins:
            existing = self.join_catalog._load(conn.name)
            from dbaide.joins.catalog import relation_endpoint_key
            existing_keys = set()
            for j in existing:
                try:
                    existing_keys.add(relation_endpoint_key(
                        j.get("table", ""), j.get("column", ""),
                        j.get("ref_table", ""), j.get("ref_column", ""),
                    ))
                except Exception:
                    pass
            added = 0
            for j in imported_joins:
                try:
                    key = relation_endpoint_key(
                        j.get("table", ""), j.get("column", ""),
                        j.get("ref_table", ""), j.get("ref_column", ""),
                    )
                except Exception:
                    continue
                if key not in existing_keys:
                    existing.append(j)
                    existing_keys.add(key)
                    added += 1
            if added:
                self.join_catalog._save(conn.name, existing)

        # Merge annotations (upsert by scope+database+table+column).
        imported_anns = data.get("annotations") or []
        if imported_anns:
            for ann in imported_anns:
                scope = str(ann.get("scope") or "table")
                database = str(ann.get("database") or "")
                table = str(ann.get("table") or "")
                column = str(ann.get("column") or "")
                note = str(ann.get("note") or "")
                if not note:
                    continue
                self.annotations.add(
                    conn.name, scope=scope, database=database,
                    table=table, column=column, note=note,
                    source=str(ann.get("source") or "user"),
                )

        self.cfg.reload()
        return {"name": conn.name, "joins_count": len(imported_joins), "annotations_count": len(imported_anns)}

    def export_all(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Export all connections, models, joins, annotations and resource defaults."""
        from datetime import datetime, timezone

        connections: list[dict[str, Any]] = []
        all_joins: dict[str, list] = {}
        all_annotations: dict[str, list] = {}

        for name, conn in self.cfg.connections().items():
            conn_dict: dict[str, Any] = {
                "name": conn.name, "type": conn.type, "database": conn.database,
                "host": conn.host, "port": conn.port, "user": conn.user,
                "password_env": conn.password_env, "password": conn.password,
                "path": conn.path,
                "load_profile": conn.load_profile, "session_timezone": conn.session_timezone,
                "sslmode": getattr(conn, "sslmode", ""), "ssl_ca": getattr(conn, "ssl_ca", ""),
            }
            conn_dict = {k: v for k, v in conn_dict.items() if v not in (None, "", 0)}
            conn_dict.setdefault("name", conn.name)
            conn_dict.setdefault("type", conn.type)
            connections.append(conn_dict)

            joins = self.join_catalog._load(name)
            if joins:
                all_joins[name] = joins
            annotations = self.annotations._load(name)
            if annotations:
                all_annotations[name] = annotations

        models: list[dict[str, Any]] = []
        for name, model in self.cfg.models().items():
            m_dict: dict[str, Any] = {
                "name": model.name, "provider": model.provider,
                "base_url": model.base_url, "api_key_env": model.api_key_env,
                "api_key": model.api_key,
                "model": model.model, "timeout_seconds": model.timeout_seconds,
                "context_length": model.context_length,
            }
            m_dict = {k: v for k, v in m_dict.items() if v not in (None, "", 0)}
            m_dict.setdefault("name", name)
            models.append(m_dict)

        resource_defaults = self.cfg.resource_defaults()

        return {
            "dbaide_export": {
                "version": 1,
                "type": "full",
                "exported_at": datetime.now(timezone.utc).isoformat(),
            },
            "connections": connections,
            "models": models,
            "resource_defaults": resource_defaults,
            "joins": all_joins,
            "annotations": all_annotations,
        }

    def _import_full(self, data: dict[str, Any]) -> dict[str, Any]:
        """Import a full export (all connections + models + resource defaults)."""
        from dbaide.config import _CONNECTION_KEYS, _MODEL_KEYS
        conn_count = 0
        model_count = 0

        for conn_data in (data.get("connections") or []):
            name = str(conn_data.get("name") or "")
            if not name:
                continue
            conn_payload = {k: v for k, v in conn_data.items() if k in _CONNECTION_KEYS}
            conn_payload.setdefault("name", name)
            conn_payload.setdefault("type", "")
            try:
                conn = ConnectionConfig(**conn_payload)
                self.cfg.upsert_connection(conn, make_default=False)
                conn_count += 1
            except (TypeError, ValueError):
                continue

        for model_data in (data.get("models") or []):
            name = str(model_data.get("name") or "")
            if not name:
                continue
            m_payload = {k: v for k, v in model_data.items() if k in _MODEL_KEYS}
            m_payload.setdefault("name", name)
            try:
                model = ModelConfig(**m_payload)
                self.cfg.upsert_model(model, make_default=False)
                model_count += 1
            except (TypeError, ValueError):
                continue

        # Import per-connection joins and annotations.
        all_joins = data.get("joins") or {}
        if isinstance(all_joins, dict):
            for instance, joins_list in all_joins.items():
                if isinstance(joins_list, list) and joins_list:
                    existing = self.join_catalog._load(instance)
                    from dbaide.joins.catalog import relation_endpoint_key
                    existing_keys = set()
                    for j in existing:
                        try:
                            existing_keys.add(relation_endpoint_key(
                                j.get("table", ""), j.get("column", ""),
                                j.get("ref_table", ""), j.get("ref_column", ""),
                            ))
                        except Exception:
                            pass
                    for j in joins_list:
                        try:
                            key = relation_endpoint_key(
                                j.get("table", ""), j.get("column", ""),
                                j.get("ref_table", ""), j.get("ref_column", ""),
                            )
                        except Exception:
                            continue
                        if key not in existing_keys:
                            existing.append(j)
                            existing_keys.add(key)
                    self.join_catalog._save(instance, existing)

        all_anns = data.get("annotations") or {}
        if isinstance(all_anns, dict):
            for instance, anns_list in all_anns.items():
                if isinstance(anns_list, list):
                    for ann in anns_list:
                        note = str(ann.get("note") or "")
                        if not note:
                            continue
                        self.annotations.add(
                            instance,
                            scope=str(ann.get("scope") or "table"),
                            database=str(ann.get("database") or ""),
                            table=str(ann.get("table") or ""),
                            column=str(ann.get("column") or ""),
                            note=note,
                            source=str(ann.get("source") or "user"),
                        )

        rd = data.get("resource_defaults")
        if isinstance(rd, dict) and rd:
            self.cfg.set_resource_defaults(rd)

        self.cfg.reload()
        return {"connections": conn_count, "models": model_count}

    # ── Backup ────────────────────────────────────────────────────────────────

    def backup_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        from dbaide.backup import BackupEngine
        conn = self.cfg.get_connection(str(payload.get("connection_name") or ""))
        engine = BackupEngine(conn)
        database = str(payload.get("database") or "")
        table = str(payload.get("table") or "")
        fmt = str(payload.get("format") or "csv")
        batch_size = int(payload.get("batch_size") or 5000)
        threads = int(payload.get("threads") or 4)
        progress = payload.get("progress")

        def on_progress(tbl: str, done: int, total: object) -> None:
            if progress:
                progress({"table": tbl, "done": done, "total": total})

        scope = str(payload.get("scope") or "table")
        if scope == "table":
            result = engine.backup_table(
                database, table,
                fmt=fmt, batch_size=batch_size,
                on_progress=on_progress,
            )
            return {"results": [result]}
        results = engine.backup_database(
            database,
            fmt=fmt, batch_size=batch_size,
            threads=threads,
            on_progress=on_progress,
        )
        return {"results": results}

    def backup_list(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from dbaide.backup import BackupRegistry
        registry = BackupRegistry()
        records = registry.list_backups()
        return {"records": [_to_dict(r) for r in records]}

    def backup_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        from dbaide.backup import BackupRegistry
        registry = BackupRegistry()
        backup_id = int(payload.get("id") or 0)
        registry.delete(backup_id)
        return {"deleted": backup_id}

    def _safe_llm(self):
        return build_llm_client(self.cfg.model())
