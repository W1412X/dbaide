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

import time
from dataclasses import dataclass, field

from dbaide.agent.progress_events import agent_label, normalize_trace_key, phase_for

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
        return agent_label(self.agent) if self.agent else ""

    # Backwards-compatible alias: a tool node's children are its sub-agent steps.
    @property
    def substeps(self) -> list["TraceNode"]:
        return self.children

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
        if stage == "loop":
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
                kind=kind, status=status, title=title, detail=detail,
                duration_ms=duration, step=int(event.get("step") or 0),
                started_at=self._last_ts, raw=dict(event),
            )
            if is_tool:
                node.thought = self._pending_thought
                self._pending_thought = ""
                self._stage_index[stage] = node_id
                self._last_tool_id = node_id
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
            node.raw = dict(event)

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
