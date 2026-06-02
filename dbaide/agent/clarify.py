"""Semantic clarifier — refuse to guess; confirm every uncertain business meaning.

A query runs against the user's real database, and a wrong *interpretation* yields
a confidently wrong answer. The ambiguities are open-ended and impossible to
enumerate up front — which table or column to use when several could fit, what a
status/flag/category value actually means, how a timestamp is stored and which
timezone a window refers to, what a metric counts, which rows are in scope, units,
and countless others specific to each business.

The iron rule (Codex/Claude-Code style): **never guess anything you are not certain
of.** If the correct interpretation is not unambiguous from the user's question
plus the schema/values in hand, surface it and let the user confirm — do not invent
a "sensible default" that presumes a business fact (a timezone, a status value, a
region, which table). This is not a fixed checklist; it is a stance applied to
every point of doubt, at any stage, as many times as doubt arises.

This module inspects a question against the resolved schema (and, when available,
the real observed values of its columns) and returns the genuinely-uncertain points
as questions — grounded in real candidates where possible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dbaide.llm import LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo

if TYPE_CHECKING:
    from dbaide.llm import LLMClient

_MAX_QUESTIONS = 6


@dataclass(slots=True)
class ClarificationPlan:
    """Genuinely-uncertain points that must be confirmed before SQL is written."""

    questions: list[dict[str, Any]] = field(default_factory=list)  # {ask, options}
    assumptions: list[str] = field(default_factory=list)           # facts CERTAIN from schema/values

    def is_empty(self) -> bool:
        return not self.questions

    def first_options(self) -> list[str]:
        if self.questions:
            return [str(o) for o in (self.questions[0].get("options") or []) if str(o).strip()]
        return []

    def render_question(self) -> str:
        """A single prompt enumerating every point that needs the user's confirmation."""
        lines = [
            "Before I run this I need you to confirm a few definitions — I won't guess "
            "these, because a wrong interpretation would give a wrong number:",
        ]
        for i, q in enumerate(self.questions, 1):
            ask = str(q.get("ask") or "").strip()
            opts = [str(o) for o in (q.get("options") or []) if str(o).strip()]
            line = f"\n**{i}. {ask}**"
            if opts:
                line += "\n   Options: " + " · ".join(f"`{o}`" for o in opts)
            lines.append(line)
        lines.append("\nTell me your choice for each (anything you leave open, I'll ask again rather than assume).")
        return "\n".join(lines)

    def render_assumptions(self) -> str:
        if not self.assumptions:
            return ""
        return "Confirmed from the schema: " + "; ".join(self.assumptions)


def _schema_digest(disclosed: list[tuple[str, str, list[ColumnInfo]]]) -> str:
    blocks: list[str] = []
    for db, table, columns in disclosed:
        label = f"{db}.{table}" if db else table
        cols = []
        for c in columns[:60]:
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


def _values_digest(observed_values: dict[str, list[str]] | None) -> str:
    if not observed_values:
        return ""
    lines = ["Observed values (a sample) for some columns — use these as the real candidates:"]
    for key, vals in observed_values.items():
        shown = ", ".join(str(v) for v in vals[:25])
        suffix = " …" if len(vals) > 25 else ""
        lines.append(f"  {key}: {shown}{suffix}")
    return "\n".join(lines)


class SemanticClarifier:
    """Surfaces every genuinely-uncertain interpretation for a question + schema."""

    def __init__(self, llm: "LLMClient") -> None:
        self.llm = llm

    def analyze(
        self,
        question: str,
        disclosed: list[tuple[str, str, list[ColumnInfo]]],
        observed_values: dict[str, list[str]] | None = None,
        already_confirmed: list[str] | None = None,
    ) -> ClarificationPlan:
        if isinstance(self.llm, NullLLMClient) or not question.strip() or not disclosed:
            return ClarificationPlan()
        system = (
            "You are a rigorous data analyst. The SQL you help write will run against the "
            "user's REAL database, and a wrong INTERPRETATION of the question produces a "
            "confidently wrong number that misleads them.\n\n"
            "IRON RULE: never guess anything you are not certain of. If the correct "
            "interpretation is not unambiguous from the question plus the schema/values you "
            "are given, you MUST ask the user to confirm it — do not assume.\n\n"
            "What counts as 'uncertain' is OPEN-ENDED and specific to this question and schema "
            "— it is NOT a fixed checklist. Examine everything that a reasonable person could "
            "read more than one way and that would change the result, for example (illustrative, "
            "not exhaustive): which table or column to use when several plausibly fit; the exact "
            "meaning/encoding of a value (which status/flag/type values qualify for the concept "
            "in the question); how a timestamp is stored and which timezone a date window means; "
            "what a metric counts (numerator/denominator, de-duplication, what qualifies); which "
            "rows are in scope (test, soft-deleted, cancelled, non-active); units; granularity.\n\n"
            "HARD CONSTRAINTS:\n"
            "- Do NOT invent a default that presumes a business fact (never assume a timezone, a "
            "status value, a region, or which table — ask instead).\n"
            "- Provide concrete options ONLY when you genuinely know the real candidates (the "
            "observed values you were given, or the tables/columns in the schema). When you "
            "don't know the candidates, ask an open question with no options.\n"
            "- Only raise points that are genuinely uncertain AND would change the result; skip "
            "what is already unambiguous. If everything is clear, return no questions.\n"
            "- 'assumptions' may contain ONLY facts that are CERTAIN from the schema/values "
            "(e.g. 'amount is a numeric column'), never a guess about meaning.\n"
            f"Return at most {_MAX_QUESTIONS} questions. JSON only."
        )
        confirmed = [str(c).strip() for c in (already_confirmed or []) if str(c).strip()]
        confirmed_block = ""
        if confirmed:
            confirmed_block = (
                "\nAlready confirmed with the user — do NOT ask these again:\n"
                + "\n".join(f"  - {c}" for c in confirmed) + "\n"
            )
        user = (
            f"Question:\n{question}\n\n"
            f"Resolved schema (only these tables/columns are in play):\n{_schema_digest(disclosed)}\n"
            + (f"\n{_values_digest(observed_values)}\n" if observed_values else "")
            + confirmed_block
            + '\nReturn {"questions":[{"ask":"...","options":["..."]}], "assumptions":["..."]}. '
            "Empty questions means every interpretation is already unambiguous."
        )
        try:
            payload = self.llm.complete_json(
                [LLMMessage("system", system), LLMMessage("user", user)],
                schema_hint='{"questions":[{"ask","options"}],"assumptions":[]}',
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
                "ask": ask,
                "options": [str(o) for o in (q.get("options") or []) if str(o).strip()],
            })
        assumptions = [str(a).strip() for a in (payload.get("assumptions") or []) if str(a).strip()]
        return ClarificationPlan(questions=questions, assumptions=assumptions)
