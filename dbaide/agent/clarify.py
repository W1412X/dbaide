"""Semantic clarifier — pins down the *business 口径* (exact criteria) of a question
before any SQL is written.

Text-to-SQL goes quietly wrong not on syntax but on *meaning*: a time window the
user thinks of in their local zone but the column stores in UTC; a "refund rate"
that may or may not require successful delivery first; whether NULLs are excluded
or counted as zero; whether test/soft-deleted rows are in scope. These are not
schema questions — the columns exist — they are *definition* questions, and
guessing them produces a confidently wrong number.

Borrowing the Codex/Claude-Code stance: be rigorous, don't guess on anything that
materially changes the result — surface it and ask. This module inspects the
question against the resolved schema and returns the *material* ambiguities as
targeted questions (each with concrete options + a sensible default), so the agent
can confirm the criteria with the user before generating SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dbaide.llm import LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo

if TYPE_CHECKING:
    from dbaide.llm import LLMClient

# Dimensions we explicitly reason about (the prompt enumerates these so the model
# checks each rather than only the obvious one).
DIMENSIONS = ("time", "metric", "null", "scope")

_MAX_QUESTIONS = 4


@dataclass(slots=True)
class ClarificationPlan:
    """Material business-criteria ambiguities found in a question."""

    questions: list[dict[str, Any]] = field(default_factory=list)  # {dimension, ask, options, default}
    assumptions: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.questions

    def first_options(self) -> list[str]:
        if self.questions:
            return [str(o) for o in (self.questions[0].get("options") or []) if str(o).strip()]
        return []

    def render_question(self) -> str:
        """A single markdown prompt enumerating every ambiguity for the user."""
        lines = [
            "Before I run this, I need to pin down a couple of business definitions so "
            "the numbers mean exactly what you expect:",
        ]
        for i, q in enumerate(self.questions, 1):
            ask = str(q.get("ask") or "").strip()
            opts = [str(o) for o in (q.get("options") or []) if str(o).strip()]
            default = str(q.get("default") or "").strip()
            line = f"\n**{i}. {ask}**"
            if opts:
                shown = []
                for o in opts:
                    shown.append(f"`{o}` (default)" if default and o == default else f"`{o}`")
                line += "\n   Options: " + " · ".join(shown)
            lines.append(line)
        lines.append(
            "\nReply with your choices (or just say “use the defaults”), and I'll apply them precisely."
        )
        return "\n".join(lines)

    def render_assumptions(self) -> str:
        if not self.assumptions:
            return ""
        return "Assumptions applied: " + "; ".join(self.assumptions)


def _schema_digest(disclosed: list[tuple[str, str, list[ColumnInfo]]]) -> str:
    """Compact schema view (name : type, nullable, comment) the model reasons over."""
    blocks: list[str] = []
    for db, table, columns in disclosed:
        label = f"{db}.{table}" if db else table
        cols = []
        for c in columns[:40]:
            tags = []
            if getattr(c, "nullable", None):
                tags.append("nullable")
            if getattr(c, "primary_key", False):
                tags.append("pk")
            tag = f" [{', '.join(tags)}]" if tags else ""
            comment = (getattr(c, "comment", "") or "").strip()
            note = f" — {comment[:60]}" if comment else ""
            cols.append(f"    {c.name}: {c.data_type or '?'}{tag}{note}")
        blocks.append(f"  {label}\n" + "\n".join(cols))
    return "\n".join(blocks)


class SemanticClarifier:
    """Detects material business-criteria ambiguities for a question + schema."""

    def __init__(self, llm: "LLMClient") -> None:
        self.llm = llm

    def analyze(
        self, question: str, disclosed: list[tuple[str, str, list[ColumnInfo]]]
    ) -> ClarificationPlan:
        if isinstance(self.llm, NullLLMClient) or not question.strip() or not disclosed:
            return ClarificationPlan()
        system = (
            "You are a meticulous data analyst. Before any SQL is written, your job is to "
            "make the BUSINESS DEFINITION (口径) of the question unambiguous. Text-to-SQL "
            "fails on meaning, not syntax — a wrong definition yields a confidently wrong "
            "number. Be rigorous: do NOT guess on anything that would materially change the "
            "result; surface it as a question instead.\n\n"
            "Check these dimensions against the schema and the question:\n"
            "• TIME — if a date/time column is involved: is it stored in UTC or local time, and "
            "which timezone should the window use? Are window bounds inclusive/exclusive? Which "
            "timestamp column defines the event?\n"
            "• METRIC DEFINITION — for any rate/ratio/count/aggregate: what exactly qualifies? "
            "(e.g. a refund rate — only refunds AFTER successful delivery, or any refund? "
            "numerator/denominator? de-duplication? which status values count?)\n"
            "• NULL / MISSING — for nullable columns that affect the result: exclude NULLs, treat "
            "as zero, or count them?\n"
            "• SCOPE / FILTERS — should test rows, soft-deleted (del_flag), cancelled, or "
            "non-active rows be excluded?\n\n"
            "Only raise a question when (a) it is genuinely ambiguous from the question text, and "
            "(b) different reasonable answers give different results. Skip anything already clear. "
            "Give each question 2–4 concrete options and a sensible default. Return at most "
            f"{_MAX_QUESTIONS} questions. Anything you decide WITHOUT asking, record as an "
            "assumption. Return JSON only."
        )
        user = (
            f"Question:\n{question}\n\n"
            f"Resolved schema (only these tables/columns are in play):\n{_schema_digest(disclosed)}\n\n"
            'Return {"questions":[{"dimension":"time|metric|null|scope","ask":"...",'
            '"options":["..."],"default":"..."}], "assumptions":["..."]}. '
            "Empty questions means the 口径 is already unambiguous."
        )
        try:
            payload = self.llm.complete_json(
                [LLMMessage("system", system), LLMMessage("user", user)],
                schema_hint='{"questions":[{"dimension","ask","options","default"}],"assumptions":[]}',
            )
        except Exception:
            return ClarificationPlan()
        if not isinstance(payload, dict):
            return ClarificationPlan()
        questions = []
        for q in (payload.get("questions") or [])[:_MAX_QUESTIONS]:
            if not isinstance(q, dict):
                continue
            ask = str(q.get("ask") or "").strip()
            if not ask:
                continue
            questions.append({
                "dimension": str(q.get("dimension") or "").strip(),
                "ask": ask,
                "options": [str(o) for o in (q.get("options") or []) if str(o).strip()],
                "default": str(q.get("default") or "").strip(),
            })
        assumptions = [str(a).strip() for a in (payload.get("assumptions") or []) if str(a).strip()]
        return ClarificationPlan(questions=questions, assumptions=assumptions)
