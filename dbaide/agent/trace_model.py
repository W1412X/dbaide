"""TraceModel: turn a stream of progress events into the execution tree.

The agent loop and its sub-agents emit flat progress dicts. ``TraceModel`` assembles
them into a tree that mirrors how the system actually ran:

  * top-level nodes are the agent loop's tool calls (one per step), in order;
  * each tool's sub-agents (schema discovery, join validation, risk, …) hang under
    it, and units of work that ran *in parallel* (e.g. one node per database scanned,
    one per join checked) appear as **sibling** nodes at the same level;
  * a node carries everything needed to inspect it (title, phase, status, duration,
    the raw event), so a UI can show details on click.

Identity comes from each event's ``node_id``/``parent_id`` when present (so repeated
events for one unit of work update a single node), otherwise it is derived from the
step index / stage / parent. Pure Python (no Qt) so it is fully unit-testable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from dbaide.agent.progress_events import agent_label, normalize_trace_key, phase_for, step_type

ROOT_ID = "__root__"
_ACTIVE = "running"
_TERMINAL = {"completed", "failed", "waiting"}

# Workflow envelope / post-hoc summaries — not real execution steps. The agent loop
# already records generate_sql, validate_sql, execute_sql, substeps, and decide
# events as they happen. These stages are kept in persisted trace for older runs
# but must not appear on the timeline (they duplicate the real tool path).
_TIMELINE_HIDDEN_STAGES = frozenset({
    "workflow_started",
    "workflow_completed",
    "planning",
    "agent_request",
    "agent_progress",
    "sql_generated",
    "sql_validation",
    "execution_completed",
    "result_interpreted",
})

# When a loop tool step exists, hide workflow-level duplicates of the same work.
_TIMELINE_DEDUP_BY_TOOL = {
    "sql_generated": "generate_sql",
    "sql_validation": "validate_sql",
    "execution_completed": "execute_sql",
}


@dataclass(slots=True)
class TraceNode:
    id: str
    parent_id: str
    stage: str = ""
    phase: str = ""
    agent: str = ""
    kind: str = ""
    node_type: str = "info"
    status: str = "running"
    title: str = ""
    detail: str = ""
    duration_ms: float = 0.0
    step: int = 0
    thought: str = ""
    started_at: float = 0.0
    raw: dict = field(default_factory=dict)
    children: list["TraceNode"] = field(default_factory=list)

    @property
    def agent_name(self) -> str:
        return localized_agent_label(self.agent) if self.agent else ""

    @property
    def agents(self) -> list[str]:
        seen: list[str] = []
        for child in self.children:
            label = child.agent_name
            if label and label not in seen:
                seen.append(label)
        return seen

    def descendant_agents(self) -> list[str]:
        seen: list[str] = []
        stack = list(self.children)
        while stack:
            node = stack.pop()
            if node.agent_name and node.agent_name not in seen:
                seen.append(node.agent_name)
            stack.extend(node.children)
        return seen


@dataclass(slots=True)
class TraceTimelineEntry:
    node_id: str
    title: str
    summary: str
    status: str
    duration_ms: float
    step: int
    stage: str
    phase: str
    agent: str
    node_type: str
    detail: str
    thought: str
    depth: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    children: list["TraceTimelineEntry"] = field(default_factory=list)


class TraceModel:
    def __init__(self, *, title: str = "Workflow") -> None:
        self.title = title
        self.root = TraceNode(id=ROOT_ID, parent_id="", kind="root", status="running", title=title)
        self._index: dict[str, TraceNode] = {ROOT_ID: self.root}
        self._stage_index: dict[str, str] = {}   # stage name → tool node id
        self._last_tool_id = ROOT_ID
        self.overall = "idle"                     # idle | running | done | failed
        self.boot_phase = ""                      # summary when running but no tool steps yet
        self._pending_thought = ""
        self._first_ts = 0.0
        self._last_ts = 0.0
        self.prompt_tokens = 0

    # The top-level tool steps, in order.
    @property
    def steps(self) -> list[TraceNode]:
        return self.root.children

    def find(self, node_id: str) -> TraceNode | None:
        return self._index.get(node_id)

    # ── Ingest ────────────────────────────────────────────────────────────────

    def ingest(self, event: dict, *, now: float | None = None) -> None:
        if not isinstance(event, dict):
            return
        # Persisted trace events (TraceEvent.to_dict) carry the original rich progress
        # event under `metadata` (args, options, clarification questions, sql, …). Merge
        # it back so the node keeps full detail; the persisted top-level fields still win
        # for the display columns they own.
        meta = event.get("metadata")
        if isinstance(meta, dict) and meta:
            event = {**meta, **{k: v for k, v in event.items() if k != "metadata"}}
        ts = float(event.get("timestamp") or 0.0) or (now if now is not None else time.time())
        if self._first_ts == 0.0:
            self._first_ts = ts
        self._last_ts = ts

        stage = str(event.get("stage") or "").strip()
        status = str(event.get("status") or "").strip() or _ACTIVE
        kind = str(event.get("kind") or "").strip()
        title = str(event.get("title") or "").strip()

        # Framing events are not nodes.
        if stage in {"workflow_started", "planning"}:
            if self.overall == "idle":
                self.overall = "running"
            if status in _ACTIVE:
                self.boot_phase = title or localized_phase(stage, str(event.get("phase") or ""))
            return
        if stage == "workflow_completed":
            self.overall = "failed" if status == "failed" else "done"
            return
        if stage == "loop" and not event.get("node_id"):
            self.overall = ("failed" if status == "failed" else "done") if status in _TERMINAL else "running"
            return

        # A thought precedes the tool it justifies — hold it for the next step.
        if stage == "decision" or (kind == "decision" and not phase_for(stage) and int(event.get("step") or 0) == 0):
            if title:
                self._pending_thought = title
            pt = event.get("prompt_tokens")
            if isinstance(pt, (int, float)) and pt > 0:
                self.prompt_tokens += int(pt)
            return

        if self.overall == "idle":
            self.overall = "running"
        if status in _ACTIVE and stage in {"environment_check", "agent_request"}:
            self.boot_phase = title or localized_phase(stage, str(event.get("phase") or ""))

        node_id, parent_id, is_tool = self._identify(event, stage, kind, status, title)
        detail = str(event.get("detail") or event.get("summary") or "").strip()
        duration = float(event.get("duration_ms") or 0.0)

        node = self._index.get(node_id)
        if node is None:
            parent = self._index.get(parent_id) or self.root
            node = TraceNode(
                id=node_id, parent_id=parent.id, stage=stage,
                phase=str(event.get("phase") or "").strip() or phase_for(stage) or (stage if is_tool else ""),
                agent=str(event.get("agent") or "").strip(),
                kind=kind, node_type=step_type(event, is_tool=is_tool),
                status=status, title=title, detail=detail,
                duration_ms=duration, step=int(event.get("step") or 0),
                started_at=self._last_ts, raw=dict(event),
            )
            if is_tool:
                node.thought = self._pending_thought
                self._pending_thought = ""
                self._last_tool_id = node_id
            # Index every node's stage (last wins), so a later sub-step can nest under
            # any node by naming it as `parent` — enabling arbitrary tree depth, not
            # just tool→substep.
            if stage:
                self._stage_index[stage] = node_id
            parent.children.append(node)
            self._index[node_id] = node
        else:
            node.status = status
            if title:
                node.title = title
            if detail:
                node.detail = detail
            if duration > 0:
                node.duration_ms = duration
            # A later event may reveal the step actually ran SQL (the "Calling"
            # frame had no sql; the "done" frame does) — upgrade the type.
            new_type = step_type(event, is_tool=(node.parent_id == ROOT_ID))
            if new_type == "sql" or node.node_type in ("info", "tool"):
                node.node_type = new_type
            node.raw = dict(event)
        pt = event.get("prompt_tokens")
        if isinstance(pt, (int, float)) and pt > 0:
            self.prompt_tokens += int(pt)
        self._expand_llm_calls(node)

    def _expand_llm_calls(self, node: TraceNode) -> None:
        """Make every recorded LLM call a real child node.

        The parent action still carries the aggregate raw event; child nodes make
        each prompt/response independently visible and clickable in the UI tree.
        """
        raw = node.raw if isinstance(node.raw, dict) else {}
        calls = raw.get("llm_calls")
        if not isinstance(calls, list):
            return
        for idx, call in enumerate(calls, 1):
            if not isinstance(call, dict):
                continue
            child_id = f"{node.id}/llm:{idx}"
            title = str(call.get("stage") or node.stage or "llm").strip()
            child_raw = {
                "stage": str(call.get("stage") or "llm"),
                "title": title,
                "status": "completed",
                "kind": "llm",
                "llm_call": dict(call),
            }
            child = self._index.get(child_id)
            if child is None:
                child = TraceNode(
                    id=child_id,
                    parent_id=node.id,
                    stage=child_raw["stage"],
                    phase=child_raw["stage"],
                    kind="llm",
                    node_type="llm",
                    status="completed",
                    title=title,
                    detail=str(call.get("method") or ""),
                    duration_ms=float(call.get("ms") or 0.0),
                    started_at=self._last_ts,
                    raw=child_raw,
                )
                node.children.append(child)
                self._index[child_id] = child
            else:
                child.stage = child_raw["stage"]
                child.phase = child_raw["stage"]
                child.title = title
                child.detail = str(call.get("method") or "")
                child.duration_ms = float(call.get("ms") or 0.0)
                child.raw = child_raw

    def _identify(self, event: dict, stage: str, kind: str, status: str, title: str) -> tuple[str, str, bool]:
        explicit_id = str(event.get("node_id") or "").strip()
        explicit_parent = str(event.get("parent_id") or "").strip()
        is_substep = kind == "substep" or status == "info"
        step = int(event.get("step") or 0)

        if is_substep and not (step > 0):
            parent_id = explicit_parent or self._stage_index.get(str(event.get("parent") or "").strip()) or self._last_tool_id
            if explicit_id:
                node_id = explicit_id
            else:
                agent = str(event.get("agent") or "").strip()
                node_id = f"{parent_id}|{agent}|{normalize_trace_key(title)}"
            return node_id, parent_id, False

        # Tool / phase step.
        node_id = explicit_id or (f"step:{step}" if step > 0 else f"stage:{stage}")
        parent_id = explicit_parent or ROOT_ID
        return node_id, parent_id, True

    def finalize(self, *, failed: bool = False) -> None:
        self.overall = "failed" if failed else ("failed" if self.overall == "failed" else "done")
        # A node still running when the turn ended is resolved to the turn's outcome:
        # on failure the in-flight node is the likely culprit, so mark it failed (red),
        # not completed (green).
        resolved = "failed" if self.overall == "failed" else "completed"
        for node in self._index.values():
            if node.id != ROOT_ID and node.status == _ACTIVE:
                node.status = resolved

    # ── Derived view ──────────────────────────────────────────────────────────

    @property
    def current_step(self) -> int:
        """Agent loop iteration index (tool ``step`` field), not the UI timeline count."""
        for node in reversed(self.steps):
            if node.step > 0:
                return node.step
        return len(self.steps)

    def _current_tool(self) -> TraceNode | None:
        for node in reversed(self.steps):
            if node.status == _ACTIVE:
                return node
        return self.steps[-1] if self.steps else None

    @property
    def current_phase(self) -> str:
        node = self._current_tool()
        return node.phase if node else ""

    @property
    def active_agents(self) -> list[str]:
        node = self._current_tool()
        return node.descendant_agents() if node else []

    @property
    def total_agents(self) -> list[str]:
        return self.root.descendant_agents()

    def elapsed_ms(self, now: float | None = None) -> float:
        if self._first_ts == 0.0:
            return 0.0
        end = self._last_ts if self.overall in {"done", "failed"} else (now if now is not None else time.time())
        return max(0.0, (end - self._first_ts) * 1000.0)

# ── Plain-text export (shared by the trace panel and conversation copy) ───────

_CHIP_TYPES = {"sql", "phase", "llm", "decision", "io"}
_GLYPHS = {"completed": "✓", "failed": "✗", "running": "▶", "waiting": "⏸"}


def _t(key: str, **kwargs) -> str:
    try:
        from dbaide.i18n import t
        return t(key, **kwargs)
    except Exception:
        if kwargs:
            try:
                return key.format(**kwargs)
            except Exception:
                return key
        return key


def localized_agent_label(agent: str) -> str:
    name = str(agent or "").strip()
    if not name:
        return ""
    label = _t(f"trace.agent.{name}")
    if label != f"trace.agent.{name}":
        return label
    return agent_label(name)


def localized_status(status: str) -> str:
    status = str(status or "").strip()
    mapping = {
        "completed": _t("trace.done"),
        "done": _t("trace.done"),
        "failed": _t("trace.failed"),
        "running": _t("trace.running"),
        "waiting": _t("trace.waiting"),
        "idle": _t("trace.idle"),
        "info": "",
    }
    return mapping.get(status, status)


def localized_type(node_type: str) -> str:
    key = f"trace.type.{str(node_type or '').strip()}"
    value = _t(key)
    return "" if value == key else value


def localized_phase(stage: str, phase: str = "") -> str:
    stage = str(stage or "").strip()
    phase = str(phase or "").strip()
    for key in (f"trace.phase.{stage}", f"trace.phase.{phase}"):
        if key == "trace.phase.":
            continue
        value = _t(key)
        if value != key:
            return value
    return phase or stage


def localized_node_head(node: "TraceNode") -> str:
    raw_title = node.title or node.phase or node.stage or "step"
    stage = str(node.stage or "").strip()
    title = str(raw_title or "").strip()
    raw = node.raw if isinstance(node.raw, dict) else {}
    if raw.get("llm_call"):
        call = raw.get("llm_call") if isinstance(raw.get("llm_call"), dict) else {}
        return _t("trace.llm_call", stage=str(call.get("stage") or node.stage or "llm"))
    if stage == "decide" or node.node_type == "llm":
        return _t("trace.thinking")
    if stage == "update_agenda":
        return _t("trace.agenda")
    if stage == "build_assets" and title:
        from dbaide.i18n import localized_build_title
        return localized_build_title(title)
    if node.agent:
        agent = localized_agent_label(node.agent)
        return _t("trace.subagent", agent=agent, title=title or localized_phase(stage, node.phase))
    called_tool = title.removeprefix("Calling ").strip()
    if called_tool != title:
        return _t("trace.call_tool", tool=stage or called_tool)
    done_tool = title.removesuffix(" done").strip()
    if done_tool != title:
        tool = stage or done_tool
        return _t("trace.tool_done", tool=tool)
    chip = localized_type(node.node_type)
    base = localized_phase(stage, node.phase) or title
    return f"{chip} · {base}" if chip and node.node_type in _CHIP_TYPES else (base or title)


def _fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"


def _as_text(value: object) -> str:
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def render_trace_text(model: "TraceModel") -> str:
    """Verbose, structured plain-text export of a run — meant to fully describe the
    agent's execution for debugging: every step indented by depth with its status,
    duration, thought, the tool's INPUT args, its OUTPUT/result, the exact multi-line
    SQL (with row count / database), and any clarification question + options. Pure (no
    Qt) so it's reusable for single-run copy and whole-conversation copy."""
    timeline = build_trace_timeline(model)
    if model is None or not timeline:
        if model is not None and model.overall != "idle":
            return localized_summary_line(model)
        return ""
    lines: list[str] = [localized_summary_line(model), ""]

    for entry in timeline:
        node = model.find(entry.node_id)
        if node is None:
            continue
        _append_trace_node_lines(lines, node, entry.depth)

    return "\n".join(lines)


def _append_trace_node_lines(lines: list[str], node: "TraceNode", depth: int) -> None:
    def kv(indent: str, label: str, value: object) -> None:
        text = _as_text(value).strip()
        if not text:
            return
        if "\n" in text:
            lines.append(f"{indent}    {label}:")
            for ln in text.splitlines():
                lines.append(f"{indent}      {ln}")
        else:
            lines.append(f"{indent}    {label}: {text}")

    indent = "  " * depth
    glyph = _GLYPHS.get(node.status, "·")
    dur = f"  [{_fmt_ms(node.duration_ms)}]" if node.duration_ms else ""
    status_note = f"  ({node.status})" if node.status in ("failed", "waiting", "running") else ""
    head = localized_node_head(node)
    lines.append(f"{indent}{glyph} {head}{status_note}{dur}")
    raw = node.raw if isinstance(node.raw, dict) else {}

    if node.thought:
        kv(indent, _t("trace.field.thought"), node.thought)
    if raw.get("args"):
        kv(indent, _t("trace.field.input"), raw.get("args"))

    question = str(raw.get("question") or "").strip()
    is_ask = raw.get("stage") == "ask_user" or bool(raw.get("options")) or bool(raw.get("questions"))
    if not question and is_ask:
        question = (node.detail or "").strip()
    if question and is_ask:
        kv(indent, _t("trace.field.question"), question)
    questions = raw.get("questions")
    if isinstance(questions, list) and questions:
        lines.append(f"{indent}    {_t('trace.field.question')}:")
        for i, q in enumerate(questions, 1):
            if isinstance(q, dict):
                ask = str(q.get("ask") or "").strip()
                opts = [str(o) for o in (q.get("options") or []) if str(o).strip()]
                suffix = f"  [{' | '.join(opts)}]" if opts else ""
                lines.append(f"{indent}      {i}. {ask}{suffix}")
    options = raw.get("options")
    if isinstance(options, list) and options:
        lines.append(f"{indent}    {_t('trace.field.options')}:")
        for opt in options:
            lines.append(f"{indent}      - {opt}")

    sql = str(raw.get("sql") or "").strip()
    if sql:
        facts = []
        if raw.get("row_count") not in (None, ""):
            facts.append(_t("trace.field.rows", n=raw.get("row_count")))
        if raw.get("database"):
            facts.append(f"{_t('trace.field.database')}={raw.get('database')}")
        if facts:
            kv(indent, _t("trace.field.output"), " · ".join(facts))
        kv(indent, _t("trace.field.sql"), sql)
    elif not is_ask:
        output = str(raw.get("output") or "").strip()
        detail = (node.detail or "").strip()
        shown = output or detail
        if shown and shown not in head and shown != question:
            kv(indent, _t("trace.field.output"), shown)
    if raw.get("decision") not in (None, "", {}, []):
        kv(indent, _t("trace.field.decision"), raw.get("decision"))

    if raw.get("result_data") not in (None, "", {}, []):
        kv(indent, _t("trace.field.result_data"), raw.get("result_data"))

    single_call = raw.get("llm_call") if isinstance(raw.get("llm_call"), dict) else None
    llm_calls = [single_call] if single_call else raw.get("llm_calls")
    child_llm_nodes = any(isinstance(child.raw, dict) and child.raw.get("llm_call") for child in node.children)
    if isinstance(llm_calls, list) and llm_calls and not (raw.get("llm_calls") and child_llm_nodes):
        lines.append(f"{indent}    {_t('trace.field.llm_calls')}: {len(llm_calls)}")
        for i, call in enumerate(llm_calls, 1):
            if not isinstance(call, dict):
                continue
            ms = call.get("ms")
            head_bits = [b for b in (call.get("stage"), call.get("method"), f"{ms}ms" if ms else "") if b]
            lines.append(f"{indent}    ── {_t('trace.field.llm_calls')} {i} [{' · '.join(head_bits)}]")
            for msg in call.get("messages") or []:
                if isinstance(msg, dict):
                    kv(indent + "  ", str(msg.get("role") or "msg"), msg.get("content"))
            kv(indent + "  ", _t("trace.field.response"), call.get("response"))

    if raw:
        kv(indent, _t("trace.field.raw_event"), raw)


def build_trace_timeline(model: "TraceModel | None") -> list[TraceTimelineEntry]:
    """Flat, chronological timeline for UI — one card per execution unit.

    The underlying ``TraceModel`` tree is preserved for detail panels and
    ``render_trace_text`` export. Loop container nodes are unwrapped so tool
    calls, decisions, and substeps appear in ``started_at`` order instead of
    hiding under a single "Agent loop" row.
    """
    if model is None:
        return []
    entries: list[TraceTimelineEntry] = []

    def walk(node: TraceNode, depth: int) -> None:
        if _is_loop_container(node):
            for child in _sorted_children(node):
                walk(child, depth)
            return
        if not _should_show_in_timeline(node, model):
            for child in _sorted_children(node):
                walk(child, depth)
            return
        entries.append(_timeline_entry(node, depth=depth, nested_children=False))
        for child in _sorted_children(node):
            walk(child, depth + 1)

    for root_child in _sorted_children(model.root):
        walk(root_child, 0)
    return entries


def build_trace_tree_timeline(model: "TraceModel | None") -> list[TraceTimelineEntry]:
    """Nested timeline mirroring ``model.steps`` — kept for tests/tools."""
    if model is None:
        return []
    return [_timeline_entry(node, depth=0, nested_children=True) for node in model.steps]


def count_timeline_steps(model: "TraceModel | None") -> int:
    """Visible timeline units — one per flat trace card (drawer + footer + summary)."""
    return len(build_trace_timeline(model))


def build_trace_model_from_events(
    events: list[dict[str, Any]] | None,
    *,
    live: bool = False,
) -> TraceModel:
    """Construct a ``TraceModel`` from persisted or streaming progress events."""
    model = TraceModel()
    for event in events or []:
        if isinstance(event, dict):
            model.ingest(event)
    if not live:
        model.finalize()
    return model


def step_count_from_events(events: list[dict[str, Any]] | None, *, live: bool = False) -> int:
    """Canonical UI step count for a raw event list (matches the trace timeline)."""
    return count_timeline_steps(build_trace_model_from_events(events, live=live))


def _is_loop_container(node: TraceNode) -> bool:
    node_id = str(node.id or "")
    if node.stage == "loop":
        return True
    if node_id == "loop" or node_id.endswith(":loop"):
        return True
    return False


def _sorted_children(node: TraceNode) -> list[TraceNode]:
    return sorted(node.children, key=lambda n: (n.started_at, n.step, n.id))


def _model_has_stage(model: "TraceModel", stage: str) -> bool:
    target = str(stage or "").strip()
    if not target:
        return False
    for node in model._index.values():
        if node.id == ROOT_ID:
            continue
        if str(node.stage or "").strip() == target:
            return True
    return False


def _should_show_in_timeline(node: TraceNode, model: "TraceModel") -> bool:
    stage = str(node.stage or "").strip()
    if stage in _TIMELINE_HIDDEN_STAGES:
        return False
    mapped = _TIMELINE_DEDUP_BY_TOOL.get(stage)
    if mapped and _model_has_stage(model, mapped):
        return False
    return True


def _timeline_entry(
    node: "TraceNode",
    *,
    depth: int,
    nested_children: bool,
) -> TraceTimelineEntry:
    children = (
        [_timeline_entry(child, depth=depth + 1, nested_children=True) for child in _sorted_children(node)]
        if nested_children
        else []
    )
    return TraceTimelineEntry(
        node_id=node.id,
        title=localized_node_head(node),
        summary=_timeline_summary(node, omit_child_count=not nested_children),
        status=node.status,
        duration_ms=node.duration_ms,
        step=node.step,
        stage=node.stage,
        phase=node.phase,
        agent=node.agent_name,
        node_type=node.node_type,
        detail=node.detail,
        thought=node.thought,
        depth=depth,
        raw=dict(node.raw or {}),
        children=children,
    )


def _timeline_summary(node: "TraceNode", *, omit_child_count: bool = False) -> str:
    detail = " ".join(str(node.detail or "").split()).strip()
    if detail:
        return detail
    if node.thought:
        return " ".join(str(node.thought).split()).strip()
    if node.children and not omit_child_count:
        bits: list[str] = []
        if node.agent_name:
            bits.append(node.agent_name)
        bits.append(f"{len(node.children)} substeps")
        return " · ".join(bits)
    raw_title = str(node.title or "").strip()
    if raw_title and raw_title != localized_node_head(node):
        return " ".join(raw_title.split()).strip()
    return ""


def localized_summary_line(model: "TraceModel") -> str:
    if not model.steps and model.overall == "idle":
        return _t("trace.idle")
    if not model.steps and model.overall == "running":
        elapsed = model.elapsed_ms() / 1000.0
        phase = model.boot_phase or _t("trace.starting")
        return f"{phase} · {elapsed:.1f}s"
    elapsed = model.elapsed_ms() / 1000.0
    steps = _t("trace.steps", n=count_timeline_steps(model))
    tokens = _format_tokens(model.prompt_tokens)
    if model.overall == "done":
        parts = [_t('trace.done'), steps, f"{elapsed:.1f}s"]
        if tokens:
            parts.append(tokens)
        return " · ".join(parts)
    if model.overall == "failed":
        parts = [_t('trace.failed'), steps, f"{elapsed:.1f}s"]
        if tokens:
            parts.append(tokens)
        return " · ".join(parts)
    phase = localized_phase(model._current_tool().stage, model.current_phase) if model._current_tool() else _t("trace.running")
    agents = model.active_agents
    parts = [p for p in (steps, phase) if p]
    if agents:
        parts.append(", ".join(agents))
    return " · ".join(p for p in parts if p) + f" · {elapsed:.1f}s"


def _format_tokens(tokens: int) -> str:
    if tokens <= 0:
        return ""
    if tokens >= 1000:
        return f"~{tokens / 1000:.1f}k tok"
    return f"~{tokens} tok"


def render_events_text(events: list[dict]) -> str:
    """Build a model from a flat event list and export it as text."""
    model = TraceModel()
    for event in events or []:
        if isinstance(event, dict):
            model.ingest(event)
    model.finalize()
    return render_trace_text(model)
