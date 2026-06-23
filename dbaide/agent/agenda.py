"""In-run task list state for the Ask agent.

Codex-style planning in this app is optional and local to one run: the main loop
may decide to maintain a concise task list when the work is meaningfully
multi-step. The list is explicit state, survives pause/resume, and is visible in
the trace/UI; it is not a separate pre-routing pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

_VALID_STATUS = {"pending", "in_progress", "done", "dropped"}
_VALID_KIND = {"schema", "join", "sql", "verify", "answer", "other"}
_STATUS_ALIASES = {
    # done
    "completed": "done", "complete": "done", "finished": "done", "resolved": "done",
    "完成": "done", "已完成": "done", "做完": "done",
    # in_progress
    "in-progress": "in_progress", "progress": "in_progress", "doing": "in_progress",
    "active": "in_progress", "wip": "in_progress", "started": "in_progress",
    "进行中": "in_progress", "处理中": "in_progress", "正在进行": "in_progress", "执行中": "in_progress",
    # dropped
    "cancelled": "dropped", "canceled": "dropped", "skip": "dropped", "skipped": "dropped",
    "取消": "dropped", "已取消": "dropped", "放弃": "dropped", "已放弃": "dropped",
    "跳过": "dropped", "已跳过": "dropped",
    # pending
    "todo": "pending", "to-do": "pending", "to_do": "pending", "open": "pending",
    "not_started": "pending", "not started": "pending", "queued": "pending",
    "待开始": "pending", "待办": "pending", "未开始": "pending", "待处理": "pending", "未完成": "pending",
}

# The model doesn't always use the exact "title" key (it often sends "task", and in
# Chinese answers may use other natural keys); accept common synonyms so items aren't
# silently dropped.
_TITLE_KEYS = ("title", "task", "name", "text", "description", "desc", "step", "label")


@dataclass(slots=True)
class AgendaItem:
    id: str
    title: str
    status: str = "pending"
    kind: str = "other"
    acceptance: str = ""
    evidence_refs: list[str] = field(default_factory=list)


def normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _STATUS_ALIASES.get(text, text)
    return text if text in _VALID_STATUS else "pending"


def normalize_kind(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in _VALID_KIND else "other"


def agenda_to_dict(items: list[AgendaItem]) -> list[dict[str, Any]]:
    return [asdict(item) for item in (items or [])]


def agenda_from_dict(items: Any, *, previous: list[AgendaItem] | None = None) -> list[AgendaItem]:
    prev = list(previous or [])
    prev_by_id = {item.id: item for item in prev if item.id}
    prev_by_title = {
        _title_key(item.title, item.kind): item
        for item in prev
        if item.title
    }
    out: list[AgendaItem] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(items if isinstance(items, list) else [], 1):
        if not isinstance(raw, dict):
            continue
        title = ""
        for key in _TITLE_KEYS:
            candidate = " ".join(str(raw.get(key) or "").split()).strip()
            if candidate:
                title = candidate
                break
        if not title:
            continue
        kind = normalize_kind(raw.get("kind"))
        item_id = str(raw.get("id") or "").strip()
        if item_id and item_id in prev_by_id:
            item_id = prev_by_id[item_id].id
        elif not item_id:
            prior = prev_by_title.get(_title_key(title, kind))
            item_id = prior.id if prior is not None else f"task:{index}"
        if item_id in used_ids:
            item_id = f"{item_id}:{index}"
        used_ids.add(item_id)
        acceptance = " ".join(str(raw.get("acceptance") or "").split()).strip()
        evidence_refs = _string_list(raw.get("evidence_refs"))
        out.append(AgendaItem(
            id=item_id,
            title=title,
            status=normalize_status(raw.get("status")),
            kind=kind,
            acceptance=acceptance,
            evidence_refs=evidence_refs,
        ))
    return out


def agenda_summary(items: list[AgendaItem]) -> str:
    total = len(items or [])
    if total <= 0:
        return "no tasks"
    done = sum(1 for item in items if item.status == "done")
    active = sum(1 for item in items if item.status == "in_progress")
    pending = sum(1 for item in items if item.status == "pending")
    dropped = sum(1 for item in items if item.status == "dropped")
    parts = [f"{done}/{total} done"]
    if active:
        parts.append(f"{active} in progress")
    if pending:
        parts.append(f"{pending} pending")
    if dropped:
        parts.append(f"{dropped} dropped")
    return " · ".join(parts)


def agenda_open_items(items: list[AgendaItem]) -> list[AgendaItem]:
    return [item for item in (items or []) if item.status not in {"done", "dropped"}]


def latest_agenda_from_events(events: list[dict[str, Any]]) -> list[AgendaItem]:
    agenda: list[AgendaItem] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if str(event.get("stage") or "").strip() != "update_agenda":
            continue
        # Live events carry the structured agenda under `result_data`; the persisted /
        # reloaded trace carries it under `metadata` (result_data is flattened to a preview
        # string). Fall back to the event itself for the oldest top-level shape.
        payload = event.get("result_data") if isinstance(event.get("result_data"), dict) else None
        if payload is None:
            payload = event.get("metadata") if isinstance(event.get("metadata"), dict) else event
        agenda_payload = payload.get("agenda") if isinstance(payload, dict) else None
        if isinstance(agenda_payload, dict):
            agenda = agenda_from_dict(agenda_payload.get("items"), previous=agenda)
        elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
            agenda = agenda_from_dict(payload.get("items"), previous=agenda)
    return agenda


def _title_key(title: str, kind: str) -> str:
    return f"{str(kind or '').strip().lower()}::{str(title or '').strip().lower()}"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = " ".join(str(value or "").split()).strip()
    return [text] if text else []
