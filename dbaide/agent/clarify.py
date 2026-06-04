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

from dbaide.i18n import answer_language_directive
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


# Cap how many columns/tables we offer when falling back to the full real list.
_MAX_FALLBACK = 40

# Keywords that mark a question as asking *which column/table* — used to ground its
# options against the real schema even when the model omits the explicit `kind` tag.
_COLUMN_HINTS = ("column", "field", "字段", "哪列", "哪个列", "哪一列", "哪些列", "哪个字段")
_TABLE_HINTS = ("which table", "哪张表", "哪个表", "哪些表")


def _strip_ident(text: str) -> str:
    return str(text).strip().strip('`"[]').strip()


def _is_column_question(ask: str, kind: str) -> bool:
    k = (kind or "").strip().lower()
    if k in ("column", "columns", "field", "fields"):
        return True
    if k:  # an explicit non-column kind → trust it
        return False
    low = ask.lower()
    return any(h in low for h in _COLUMN_HINTS)


def _is_table_question(ask: str, kind: str) -> bool:
    k = (kind or "").strip().lower()
    if k in ("table", "tables"):
        return True
    if k:
        return False
    low = ask.lower()
    return any(h in low for h in _TABLE_HINTS)


def _ground_questions(
    questions: list[dict[str, Any]],
    disclosed: list[tuple[str, str, list[ColumnInfo]]],
) -> list[dict[str, Any]]:
    """Replace fabricated column/table options with the REAL ones from the resolved
    schema. The model is asked to ground its options, but it still hallucinates field
    names — so for any 'which column/table?' question we intersect its options with the
    actual schema (canonicalising case/`quotes`/table-prefixes) and, if nothing real
    survives, fall back to the table's real column list. Value/other questions are left
    untouched (their options are legitimately free text / observed values)."""
    # Build lookup maps from the real disclosed schema.
    union_cols: list[str] = []
    union_map: dict[str, str] = {}                       # lower name → canonical name
    by_table: dict[str, tuple[list[str], dict[str, str]]] = {}
    table_canon: dict[str, str] = {}                     # lower table → canonical label
    all_tables: list[str] = []
    for db, table, columns in disclosed:
        canon_t = f"{db}.{table}" if db else table
        if canon_t not in all_tables:
            all_tables.append(canon_t)
        table_canon[table.lower()] = canon_t
        table_canon[canon_t.lower()] = canon_t
        cols: list[str] = []
        cmap: dict[str, str] = {}
        for c in columns:
            low = c.name.lower()
            cmap[low] = c.name
            cols.append(c.name)
            if low not in union_map:
                union_map[low] = c.name
                union_cols.append(c.name)
        by_table[table.lower()] = (cols, cmap)
        by_table[canon_t.lower()] = (cols, cmap)

    def cols_for(table_hint: str) -> tuple[list[str], dict[str, str]]:
        if table_hint:
            entry = by_table.get(_strip_ident(table_hint).lower())
            if entry:
                return entry
        return union_cols, union_map

    def match_col(option: str, cmap: dict[str, str]) -> str | None:
        key = _strip_ident(option).lower()
        if "." in key:                                   # "orders.status" → "status"
            key = key.split(".")[-1]
        return cmap.get(key)

    grounded: list[dict[str, Any]] = []
    for q in questions:
        ask = str(q.get("ask") or "")
        kind = str(q.get("kind") or "")
        table_hint = str(q.get("table") or "")
        raw_opts = [str(o) for o in (q.get("options") or []) if str(o).strip()]
        if _is_table_question(ask, kind):
            real = []
            for o in raw_opts:
                canon = table_canon.get(_strip_ident(o).lower())
                if canon and canon not in real:
                    real.append(canon)
            options = real or all_tables[:_MAX_FALLBACK]
        elif _is_column_question(ask, kind):
            cols, cmap = cols_for(table_hint)
            real = []
            for o in raw_opts:
                canon = match_col(o, cmap)
                if canon and canon not in real:
                    real.append(canon)
            # Nothing the model offered actually exists → offer the real columns so the
            # user still picks from genuine fields instead of fabricated ones.
            options = real or cols[:_MAX_FALLBACK]
        else:
            options = raw_opts
        grounded.append({"ask": ask, "options": options})
    return grounded


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
            "- GROUND EVERY QUESTION IN THE SCHEMA YOU WERE GIVEN. The resolved schema and observed "
            "values below ARE the candidate set. For a 'which column/field?' question, every option "
            "MUST be an EXACT column name copied verbatim from the schema below — never invent or "
            "guess a name, never reword it. For a 'which table?' question, options must be exact "
            "table names. For a value question, options must be the observed values shown below.\n"
            "- For EACH question also return: `kind` — one of \"column\", \"table\", \"value\", or "
            "\"other\" — and `table`, the exact table name the question is about (when applicable). "
            "These let the options be verified against the real schema.\n"
            "- Example: for 'how many sane employees', do NOT ask an open 'which field indicates "
            "sane?' — ask 'Which column identifies a sane employee?' with kind=\"column\", "
            "table=\"employees\", and options = the real employees columns (e.g. status, "
            "mental_state, is_active). Leave 'options' empty ONLY when the answer is genuinely NOT "
            "in the schema/values (e.g. a timezone).\n"
            "- Only raise points that are genuinely uncertain AND would change the result; skip "
            "what is already unambiguous. If everything is clear, return no questions.\n"
            "- 'assumptions' may contain ONLY facts that are CERTAIN from the schema/values "
            "(e.g. 'amount is a numeric column'), never a guess about meaning.\n"
            f"{answer_language_directive()}\n"
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
            + '\nReturn {"questions":[{"ask":"...","kind":"column|table|value|other",'
            '"table":"...","options":["..."]}], "assumptions":["..."]}. '
            "Empty questions means every interpretation is already unambiguous."
        )
        try:
            payload = self.llm.complete_json(
                [LLMMessage("system", system), LLMMessage("user", user)],
                schema_hint='{"questions":[{"ask","kind","table","options"}],"assumptions":[]}',
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
                "kind": str(q.get("kind") or ""),
                "table": str(q.get("table") or ""),
                "options": [str(o) for o in (q.get("options") or []) if str(o).strip()],
            })
        # Deterministically ground column/table options against the REAL schema — the
        # model still hallucinates field names even when told not to, so we verify them
        # against the disclosed columns rather than trusting the LLM's options.
        questions = _ground_questions(questions, disclosed)
        assumptions = [str(a).strip() for a in (payload.get("assumptions") or []) if str(a).strip()]
        return ClarificationPlan(questions=questions, assumptions=assumptions)
