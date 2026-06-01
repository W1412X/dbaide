"""TraceModel: turn a stream of progress events into a structured, renderable view.

The agent loop and its sub-agents emit flat progress dicts. On their own they are
hard to follow — you can't tell *what phase* the agent is in, *which* sub-agents are
involved, or *how far along* it is. ``TraceModel`` ingests those events and maintains:

  * an ordered list of :class:`TraceStep` (one per tool call), each with its phase,
    status, duration, the thought that led to it, and nested sub-agent activity;
  * the current phase and step number;
  * which sub-agents took part in the current step;
  * elapsed time and an overall status.

It is pure Python (no Qt) so it can be unit-tested and reused by any renderer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from dbaide.agent.progress_events import agent_label, phase_for

_ACTIVE = "running"
_TERMINAL = {"completed", "failed", "waiting"}


@dataclass(slots=True)
class SubAgentActivity:
    agent: str
    label: str
    title: str
    detail: str = ""
    status: str = "info"


@dataclass(slots=True)
class TraceStep:
    key: str
    step: int
    stage: str
    phase: str
    title: str
    status: str = "running"
    detail: str = ""
    duration_ms: float = 0.0
    thought: str = ""
    started_at: float = 0.0
    substeps: list[SubAgentActivity] = field(default_factory=list)

    @property
    def agents(self) -> list[str]:
        seen: list[str] = []
        for sub in self.substeps:
            if sub.label and sub.label not in seen:
                seen.append(sub.label)
        return seen


class TraceModel:
    def __init__(self, *, title: str = "Workflow") -> None:
        self.title = title
        self.steps: list[TraceStep] = []
        self._by_key: dict[str, TraceStep] = {}
        self.overall = "idle"            # idle | running | done | failed
        self._pending_thought = ""
        self._first_ts = 0.0
        self._last_ts = 0.0

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
        detail = str(event.get("detail") or event.get("summary") or "").strip()

        # Lifecycle: the loop start/finish frames the whole turn.
        if stage == "loop":
            if status in _TERMINAL:
                self.overall = "failed" if status == "failed" else "done"
            else:
                self.overall = "running"
            return

        # A "thought" decision precedes the tool it justifies — hold it.
        if stage == "decision" or (kind == "decision" and not phase_for(stage) and event.get("step", 0) == 0):
            if title:
                self._pending_thought = title
            return

        # Sub-agent activity (nested under the current/parent step).
        if kind == "substep" or status == "info":
            self._ingest_substep(event, stage, title, detail, status)
            return

        # Build progress: collapse to a single "Building assets" step.
        if stage == "build_assets":
            self._ingest_build(title or detail, status)
            return

        # Otherwise it is a tool/phase step.
        self._ingest_step(event, stage, status, title, detail)

    def _ingest_substep(self, event: dict, stage: str, title: str, detail: str, status: str) -> None:
        if self.overall == "idle":
            self.overall = "running"
        agent = str(event.get("agent") or "").strip()
        parent = str(event.get("parent") or "").strip()
        target = self._find_step(parent) or self._find_step(stage) or self._current_step()
        if target is None:
            return
        line = title or detail
        if not line:
            return
        target.substeps.append(
            SubAgentActivity(
                agent=agent,
                label=agent_label(agent) if agent else "",
                title=line,
                detail=detail if detail and detail != line else "",
                status=status,
            )
        )

    def _ingest_build(self, message: str, status: str) -> None:
        step = self._by_key.get("build_assets")
        if step is None:
            step = TraceStep(
                key="build_assets", step=0, stage="build_assets",
                phase="Building assets", title=message or "Building assets",
                status=_ACTIVE, started_at=self._last_ts,
            )
            self.steps.append(step)
            self._by_key["build_assets"] = step
            self.overall = "running"
        if message:
            step.title = message
            step.substeps.append(SubAgentActivity(agent="", label="", title=message, status="info"))
        if status in _TERMINAL:
            step.status = status

    def _ingest_step(self, event: dict, stage: str, status: str, title: str, detail: str) -> None:
        if self.overall == "idle":
            self.overall = "running"
        step_no = int(event.get("step") or 0)
        key = f"step:{step_no}" if step_no > 0 else f"stage:{stage}"
        phase = str(event.get("phase") or "").strip() or phase_for(stage) or stage
        duration = float(event.get("duration_ms") or 0.0)

        step = self._by_key.get(key)
        if step is None:
            step = TraceStep(
                key=key, step=step_no, stage=stage, phase=phase,
                title=title or phase, status=status, detail=detail,
                duration_ms=duration, thought=self._pending_thought,
                started_at=self._last_ts,
            )
            self._pending_thought = ""
            self.steps.append(step)
            self._by_key[key] = step
        else:
            step.status = status
            if title:
                step.title = title
            if detail:
                step.detail = detail
            if duration > 0:
                step.duration_ms = duration
        if status == "failed":
            # Keep overall running; the loop frame decides done/failed.
            pass

    # ── Lookups ─────────────────────────────────────────────────────────────

    def _find_step(self, stage_or_key: str) -> TraceStep | None:
        if not stage_or_key:
            return None
        if stage_or_key in self._by_key:
            return self._by_key[stage_or_key]
        # Match by stage name (most recent first).
        for step in reversed(self.steps):
            if step.stage == stage_or_key:
                return step
        return None

    def _current_step(self) -> TraceStep | None:
        return self.steps[-1] if self.steps else None

    # ── Derived view ──────────────────────────────────────────────────────────

    @property
    def current_step(self) -> int:
        for step in reversed(self.steps):
            if step.step > 0:
                return step.step
        return len(self.steps)

    @property
    def current_phase(self) -> str:
        running = [s for s in self.steps if s.status == _ACTIVE]
        target = running[-1] if running else (self.steps[-1] if self.steps else None)
        return target.phase if target else ""

    @property
    def active_agents(self) -> list[str]:
        step = None
        for s in reversed(self.steps):
            if s.status == _ACTIVE:
                step = s
                break
        if step is None:
            step = self._current_step()
        return step.agents if step else []

    @property
    def total_agents(self) -> list[str]:
        seen: list[str] = []
        for step in self.steps:
            for label in step.agents:
                if label not in seen:
                    seen.append(label)
        return seen

    def elapsed_ms(self, now: float | None = None) -> float:
        if self._first_ts == 0.0:
            return 0.0
        end = self._last_ts if self.overall in {"done", "failed"} else (now if now is not None else time.time())
        return max(0.0, (end - self._first_ts) * 1000.0)

    def summary_line(self, now: float | None = None) -> str:
        if not self.steps and self.overall == "idle":
            return "Idle"
        elapsed = self.elapsed_ms(now) / 1000.0
        phase = self.current_phase or "Working"
        step = self.current_step
        agents = self.active_agents
        parts = [f"Step {step}" if step else "Working", phase]
        if agents:
            parts.append(f"{len(agents)} agent{'s' if len(agents) != 1 else ''}: " + ", ".join(agents))
        head = " · ".join(parts)
        if self.overall == "done":
            return f"Done · {len(self.steps)} steps · {elapsed:.1f}s"
        if self.overall == "failed":
            return f"Failed · {len(self.steps)} steps · {elapsed:.1f}s"
        return f"{head} · {elapsed:.1f}s"
