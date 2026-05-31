"""Persistent join catalog per connection (user + agent-saved candidates)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("dbaide.join_catalog")

USER_JOIN_CONFIDENCE = 0.99
AGENT_PERSIST_MIN_CONFIDENCE = 0.55
DEFAULT_JOIN_DIR = Path.home() / ".dbaide" / "joins"


def relation_endpoint_key(
    table: str,
    column: str,
    ref_table: str,
    ref_column: str,
) -> tuple[str, str, str, str]:
    return (
        str(table or "").strip().lower(),
        str(column or "").strip().lower(),
        str(ref_table or "").strip().lower(),
        str(ref_column or "").strip().lower(),
    )


def relation_to_catalog_record(
    rel: dict[str, Any],
    *,
    instance: str,
    database: str = "",
    source: str = "agent",
    join_id: str = "",
) -> dict[str, Any]:
    now = _utc_now()
    conf = float(rel.get("confidence") or 0.0)
    if source == "user":
        conf = USER_JOIN_CONFIDENCE
    return {
        "id": join_id or str(rel.get("id") or uuid.uuid4().hex[:12]),
        "instance": instance,
        "database": str(rel.get("database") or database or ""),
        "table": str(rel.get("table") or ""),
        "column": str(rel.get("column") or ""),
        "ref_table": str(rel.get("ref_table") or ""),
        "ref_column": str(rel.get("ref_column") or ""),
        "source": source,
        "confidence": round(conf, 3),
        "join_type": str(rel.get("join_type") or ""),
        "reason": str(rel.get("reason") or "")[:400],
        "validation": dict(rel.get("validation") or {}),
        "validated": bool(rel.get("validated", True)),
        "created_at": str(rel.get("created_at") or now),
        "updated_at": now,
    }


def catalog_record_to_relation(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "database": record.get("database") or "",
        "table": record.get("table"),
        "column": record.get("column"),
        "ref_table": record.get("ref_table"),
        "ref_column": record.get("ref_column"),
        "source": record.get("source") or "agent",
        "confidence": float(record.get("confidence") or 0.0),
        "join_type": record.get("join_type") or "",
        "reason": record.get("reason") or "",
        "validation": dict(record.get("validation") or {}),
        "validated": bool(record.get("validated", True)),
        "catalog": True,
    }


class JoinCatalogStore:
    """CRUD for saved join edges under ~/.dbaide/joins/instances/{instance}/."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is not None:
            self.base_dir = Path(base_dir).expanduser()
        else:
            self.base_dir = Path(os.environ.get("DBAIDE_JOINS", DEFAULT_JOIN_DIR)).expanduser()

    def instance_path(self, instance: str) -> Path:
        safe = instance.replace("/", "_").replace("\\", "_").strip() or "default"
        return self.base_dir / "instances" / safe / "joins.json"

    def list_records(
        self,
        instance: str,
        *,
        database: str = "",
        tables: list[str] | None = None,
        min_confidence: float = 0.0,
        endpoint: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        records = self._load(instance)
        table_set = {t.strip().lower() for t in (tables or []) if str(t).strip()}
        ep_key = None
        if endpoint:
            ep_key = relation_endpoint_key(
                endpoint.get("table", endpoint.get("left_table", "")),
                endpoint.get("column", endpoint.get("left_column", "")),
                endpoint.get("ref_table", endpoint.get("right_table", "")),
                endpoint.get("ref_column", endpoint.get("right_column", "")),
            )
        out: list[dict[str, Any]] = []
        for rec in records:
            if database and str(rec.get("database") or "") not in {"", database}:
                continue
            if float(rec.get("confidence") or 0) < float(min_confidence):
                continue
            if ep_key:
                key = relation_endpoint_key(
                    str(rec.get("table") or ""),
                    str(rec.get("column") or ""),
                    str(rec.get("ref_table") or ""),
                    str(rec.get("ref_column") or ""),
                )
                if key != ep_key:
                    continue
            if table_set:
                lt = str(rec.get("table") or "").lower()
                rt = str(rec.get("ref_table") or "").lower()
                if lt not in table_set and rt not in table_set:
                    continue
            out.append(dict(rec))
        out.sort(key=lambda r: float(r.get("confidence") or 0), reverse=True)
        return out

    def relations_for_tables(
        self,
        instance: str,
        tables: list[tuple[str, str]],
        *,
        database: str = "",
    ) -> list[dict[str, Any]]:
        names = [table for _, table in tables]
        records = self.list_records(instance, database=database, tables=names)
        seen: set[tuple[str, str, str, str]] = set()
        relations: list[dict[str, Any]] = []
        table_set = {t.lower() for t in names}
        for rec in records:
            lt = str(rec.get("table") or "").lower()
            rt = str(rec.get("ref_table") or "").lower()
            if lt not in table_set or rt not in table_set:
                continue
            key = relation_endpoint_key(
                str(rec.get("table") or ""),
                str(rec.get("column") or ""),
                str(rec.get("ref_table") or ""),
                str(rec.get("ref_column") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            relations.append(catalog_record_to_relation(rec))
        relations.sort(key=lambda r: float(r.get("confidence") or 0), reverse=True)
        return relations

    def add(
        self,
        instance: str,
        rel: dict[str, Any],
        *,
        source: str = "user",
        database: str = "",
    ) -> dict[str, Any]:
        records = self._load(instance)
        record = relation_to_catalog_record(rel, instance=instance, database=database, source=source)
        key = relation_endpoint_key(record["table"], record["column"], record["ref_table"], record["ref_column"])
        replaced = False
        for index, existing in enumerate(records):
            existing_key = relation_endpoint_key(
                str(existing.get("table") or ""),
                str(existing.get("column") or ""),
                str(existing.get("ref_table") or ""),
                str(existing.get("ref_column") or ""),
            )
            if existing_key == key:
                record["id"] = existing.get("id") or record["id"]
                record["created_at"] = existing.get("created_at") or record["created_at"]
                if source == "user":
                    record["confidence"] = USER_JOIN_CONFIDENCE
                    record["source"] = "user"
                elif str(existing.get("source") or "") == "user":
                    record["source"] = "user"
                    record["confidence"] = max(float(existing.get("confidence") or 0), float(record["confidence"]))
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        self._save(instance, records)
        return dict(record)

    def update(self, instance: str, join_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        records = self._load(instance)
        for index, rec in enumerate(records):
            if str(rec.get("id") or "") != str(join_id):
                continue
            updated = dict(rec)
            for key in ("database", "table", "column", "ref_table", "ref_column", "join_type", "reason"):
                if key in fields and fields[key] is not None:
                    updated[key] = str(fields[key]).strip()
            if "confidence" in fields and fields["confidence"] is not None:
                updated["confidence"] = round(float(fields["confidence"]), 3)
            if str(updated.get("source") or "") == "user":
                updated["confidence"] = USER_JOIN_CONFIDENCE
            updated["updated_at"] = _utc_now()
            records[index] = updated
            self._save(instance, records)
            return dict(updated)
        return None

    def delete(self, instance: str, *, join_id: str = "", endpoint: dict[str, str] | None = None) -> bool:
        records = self._load(instance)
        if join_id:
            new_records = [r for r in records if str(r.get("id") or "") != str(join_id)]
            if len(new_records) == len(records):
                return False
            self._save(instance, new_records)
            return True
        if endpoint:
            key = relation_endpoint_key(
                endpoint.get("table", endpoint.get("left_table", "")),
                endpoint.get("column", endpoint.get("left_column", "")),
                endpoint.get("ref_table", endpoint.get("right_table", "")),
                endpoint.get("ref_column", endpoint.get("right_column", "")),
            )
            new_records = [
                r
                for r in records
                if relation_endpoint_key(
                    str(r.get("table") or ""),
                    str(r.get("column") or ""),
                    str(r.get("ref_table") or ""),
                    str(r.get("ref_column") or ""),
                )
                != key
            ]
            if len(new_records) == len(records):
                return False
            self._save(instance, new_records)
            return True
        return False

    def persist_agent_candidates(
        self,
        instance: str,
        relations: list[dict[str, Any]],
        *,
        database: str = "",
        min_confidence: float = AGENT_PERSIST_MIN_CONFIDENCE,
    ) -> list[dict[str, Any]]:
        saved: list[dict[str, Any]] = []
        for rel in relations:
            source = str(rel.get("source") or "")
            if source == "user":
                continue
            conf = float(rel.get("confidence") or 0)
            if conf < min_confidence:
                continue
            if rel.get("catalog"):
                continue
            record = self.add(
                instance,
                {**rel, "database": rel.get("database") or database},
                source="agent",
                database=database,
            )
            saved.append(record)
        return saved

    def _load(self, instance: str) -> list[dict[str, Any]]:
        path = self.instance_path(instance)
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("join_catalog_read_failed: %s", exc)
            return []
        joins = data.get("joins") if isinstance(data, dict) else data
        if not isinstance(joins, list):
            return []
        return [dict(item) for item in joins if isinstance(item, dict)]

    def _save(self, instance: str, records: list[dict[str, Any]]) -> None:
        path = self.instance_path(instance)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "instance": instance,
            "schema_version": 1,
            "updated_at": _utc_now(),
            "joins": records,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def merge_relation_layers(*layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge join lists; first layers win on duplicate endpoints (higher priority first)."""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for layer in layers:
        for rel in layer:
            key = relation_endpoint_key(
                str(rel.get("table") or ""),
                str(rel.get("column") or ""),
                str(rel.get("ref_table") or ""),
                str(rel.get("ref_column") or ""),
            )
            rev = (key[2], key[3], key[0], key[1])
            if not key[0] or key in seen or rev in seen:
                continue
            seen.add(key)
            merged.append(dict(rel))
    merged.sort(key=lambda r: float(r.get("confidence") or 0), reverse=True)
    return merged


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
