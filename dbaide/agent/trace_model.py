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

from dbaide.agent.progress_events import agent_label, normalize_trace_key, phase_for, step_type

ROOT_ID = "__root__"
_ACTIVE = "running"
_TERMINAL = {"completed", "failed", "waiting"}


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


class TraceModel:
    def __init__(self, *, title: str = "Workflow") -> None:
        self.title = title
        self.root = TraceNode(id=ROOT_ID, parent_id="", kind="root", status="running", title=title)
        self._index: dict[str, TraceNode] = {ROOT_ID: self.root}
        self._stage_index: dict[str, str] = {}   # stage name → tool node id
        self._last_tool_id = ROOT_ID
        self.overall = "idle"                     # idle | running | done | failed
        self._pending_thought = ""
        self._first_ts = 0.0
        self._last_ts = 0.0

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
            return

        if self.overall == "idle":
            self.overall = "running"

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

    def summary_line(self, now: float | None = None) -> str:
        if not self.steps and self.overall == "idle":
            return "Idle"
        elapsed = self.elapsed_ms(now) / 1000.0
        if self.overall == "done":
            return f"Done · {len(self.steps)} steps · {elapsed:.1f}s"
        if self.overall == "failed":
            return f"Failed · {len(self.steps)} steps · {elapsed:.1f}s"
        phase = self.current_phase or "Working"
        agents = self.active_agents
        parts = [f"Step {self.current_step}" if self.current_step else "Working", phase]
        if agents:
            parts.append(f"{len(agents)} agent{'s' if len(agents) != 1 else ''}: " + ", ".join(agents))
        return " · ".join(parts) + f" · {elapsed:.1f}s"


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
    if stage == "intent" and (node.id == "intent:decompose" or raw.get("llm_calls") or title == "Decompose intent"):
        return _t("trace.intent")
    if stage == "decide" or node.node_type == "llm":
        return _t("trace.thinking")
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


def _node_head(node: "TraceNode") -> str:
    return localized_node_head(node)


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
    if model is None or not model.steps:
        return ""
    lines: list[str] = [localized_summary_line(model), ""]

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

    def walk(node: "TraceNode", depth: int) -> None:
        indent = "  " * depth
        glyph = _GLYPHS.get(node.status, "·")
        dur = f"  [{_fmt_ms(node.duration_ms)}]" if node.duration_ms else ""
        status_note = f"  ({node.status})" if node.status in ("failed", "waiting", "running") else ""
        head = _node_head(node)
        lines.append(f"{indent}{glyph} {head}{status_note}{dur}")
        raw = node.raw if isinstance(node.raw, dict) else {}

        if node.thought:
            kv(indent, _t("trace.field.thought"), node.thought)
        # Tool INPUT.
        if raw.get("args"):
            kv(indent, _t("trace.field.input"), raw.get("args"))

        # Clarification: the question being asked + the candidate options, and the
        # full structured per-question list when present (this is the bit that was
        # missing from copies before).
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

        # Tool OUTPUT — exact SQL (with facts) takes precedence; else the result detail.
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
        elif not is_ask:  # clarification nodes already printed their question/options
            output = str(raw.get("output") or "").strip()
            detail = (node.detail or "").strip()
            shown = output or detail
            if shown and shown not in head and shown != question:
                kv(indent, _t("trace.field.output"), shown)
        if raw.get("decision") not in (None, "", {}, []):
            kv(indent, _t("trace.field.decision"), raw.get("decision"))

        # Debug trace: the full structured tool result (discovery hits, resolved
        # schema, relations, …) — the intermediate output passed between stages.
        if raw.get("result_data") not in (None, "", {}, []):
            kv(indent, _t("trace.field.result_data"), raw.get("result_data"))

        # Debug trace: the full prompt+response of every model call this step made.
        single_call = raw.get("llm_call") if isinstance(raw.get("llm_call"), dict) else None
        llm_calls = [single_call] if single_call else raw.get("llm_calls")
        child_llm_nodes = any(isinstance(child.raw, dict) and child.raw.get("llm_call") for child in node.children)
        if isinstance(llm_calls, list) and llm_calls and not (raw.get("llm_calls") and child_llm_nodes):
            lines.append(f"{indent}    {_t('trace.field.llm_calls')}: {len(llm_calls)}")
            for i, call in enumerate(llm_calls, 1):
                if not isinstance(call, dict):
                    continue
                ms = call.get("ms")
                head_bits = [b for b in (call.get("stage"), call.get("method"),
                                         f"{ms}ms" if ms else "") if b]
                lines.append(f"{indent}    ── {_t('trace.field.llm_calls')} {i} [{' · '.join(head_bits)}]")
                for msg in call.get("messages") or []:
                    if isinstance(msg, dict):
                        kv(indent + "  ", str(msg.get("role") or "msg"), msg.get("content"))
                kv(indent + "  ", _t("trace.field.response"), call.get("response"))

        if raw:
            kv(indent, _t("trace.field.raw_event"), raw)

        for child in node.children:
            walk(child, depth + 1)

    for node in model.steps:
        walk(node, 0)
    return "\n".join(lines)


def localized_summary_line(model: "TraceModel") -> str:
    if not model.steps and model.overall == "idle":
        return _t("trace.idle")
    elapsed = model.elapsed_ms() / 1000.0
    steps = _t("trace.steps", n=len(model.steps))
    if model.overall == "done":
        return f"{_t('trace.done')} · {steps} · {elapsed:.1f}s"
    if model.overall == "failed":
        return f"{_t('trace.failed')} · {steps} · {elapsed:.1f}s"
    phase = localized_phase(model._current_tool().stage, model.current_phase) if model._current_tool() else _t("trace.running")
    current = _t("trace.step", n=model.current_step) if model.current_step else _t("trace.running")
    agents = model.active_agents
    parts = [current, phase]
    if agents:
        parts.append(", ".join(agents))
    return " · ".join(p for p in parts if p) + f" · {elapsed:.1f}s"


def render_events_text(events: list[dict]) -> str:
    """Build a model from a flat event list and export it as text."""
    model = TraceModel()
    for event in events or []:
        if isinstance(event, dict):
            model.ingest(event)
    model.finalize()
    return render_trace_text(model)
