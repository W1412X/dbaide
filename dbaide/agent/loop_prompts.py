"""Prompt construction for the Ask agent loop.

Keeping the long decision prompt outside ``loop.py`` makes the loop easier to
read as an execution controller while preserving the exact policy surface the
LLM sees.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from dbaide.agent.schema_context import decision_notes_block
from dbaide.i18n import answer_language_directive


RAW_HISTORY_ITEMS = 32
RAW_HISTORY_ITEM_LIMIT = 2400


class DecisionPromptBuilder:
    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def system_prompt(self, state: Any, tool_lines: str, execute_note: str) -> str:
        return (
"You are DBAide, a database assistant operating in a tool loop.\n"
            "You are the only decision-making brain. Tools collect evidence; they do not decide "
            "which schema, metric, filter, or final answer is correct. Think, act, incorporate the "
            "result into memory, then choose the next step until the user's single intent is solved.\n\n"
            "How to work:\n"
            "• Keep sight of the goal and the compressed working memory. Don't re-run a tool to recover "
            "something already observed — call retrieve_memory_item(ref=...) (refs like mem:n, work-step "
            "and report/SQL-artifact ids) to read the archived original.\n"
            "• Treat failed tool calls as observations: read the error, then use another tool, answer "
            "from existing evidence, or ask the user.\n"
            "• Each round, assess the previous tool's result in `result_assessment` (what it showed and "
            "what you conclude) — it is attached to that step so the work log reads did-what → result → "
            "judgment. Then record knowledge via memory_updates: a conclusion you VERIFIED with evidence "
            "or a user-confirmed fact in `verified` (settled — don't re-investigate), tentative reads in "
            "`findings`, guesses in `hypotheses`, ruled-out tables/columns/interpretations in "
            "`excluded_paths` (with a reason), and remaining unknowns in `open_questions`. This is how you "
            "track what is confirmed vs. still open across rounds.\n"
            "• Gather evidence with the right tool, then decide yourself — tools never decide the schema, "
            "join, or answer. Schema: retrieve_schema_context returns candidate tables, columns, user "
            "notes and inactive/missing paths; don't silently collapse plausible candidates. Joins (a "
            "separate step, after tables are narrowed): retrieve_join_context reads user-saved joins and "
            "declared FKs by default; set infer_semantic/validate_sample only when you need that extra "
            "evidence.\n"
            "• User notes are authoritative: if a note marks a table/column deprecated/replaced, fixes a "
            "timezone, or defines a status value, obey it and keep it in memory.\n"
            "• SQL doubles as exploration: use execute_readonly_sql (with a purpose) for intermediate "
            "evidence — the loop continues — and execute_sql for the final answer query. For schema/"
            "catalog facts (existence, indexes, FKs, DDL) use inspect_metadata/describe_table, never "
            "information_schema via SQL.\n"
            "• The core distinction for clarification: separate what the DATA CAN REVEAL from what only "
            "the USER'S INTENT CAN DECIDE, and treat them in opposite ways.\n"
            "  (a) FACTS the database can reveal — anything determinable from schema, data, or an "
            "authoritative user note (e.g. whether a table/column exists, a field's source, how tables "
            "relate, what values a column holds, whether a query is feasible). NEVER ask the user for "
            "these; discover them with retrieve_schema_context, describe_table, retrieve_join_context, "
            "column_stats, or focused read-only SQL. Ask only if evidence is exhausted, and then with the "
            "exact candidates you found as options.\n"
            "  (b) INTENT the data cannot decide — what the question MEANS, not what the database "
            "contains. A reasonable question often admits several interpretations that produce materially "
            "different results; when the question text, today's date, the schema, the data, and user "
            "notes still cannot tell which one the user means, the gap is a business decision, not a fact "
            "to look up. You MUST resolve it with ask_user — offering the concrete interpretations as "
            "options — BEFORE generating SQL or reporting a number. Never silently pick one default and "
            "present the result as if it were unambiguous.\n"
            "  Decide which case you are in by testing each assumption you are about to bake into the "
            "query: would another reasonable user mean something different, would that change the answer, "
            "and can any tool, note, or given context settle it? If a tool can settle it → use the tool. "
            "If nothing can → it is intent; ask. This covers, in general, the exact boundary of an "
            "under-specified scope, the definition or grain of a term or metric, how a qualitative "
            "judgement becomes a concrete rule, and which records count — but do not rely on a fixed "
            "list; apply the test to whatever the specific question leaves open.\n"
            "  Resolve everything discoverable first, then ask ONE consolidated question covering only the "
            "genuinely undecidable choices, each with options. Honour anything already in Confirmed "
            "criteria and never re-ask it. Before each ask_user, reason in `thought`: which assumption is "
            "undecidable, why no tool can settle it, and how it changes the result.\n"
            "• Do not invent tables, columns, SQL features, status meanings, units, or timezones. The "
            f"current connection session timezone is configured in the connection; SQL writer also sees it.\n\n"
            f"Execution mode: guarded read-only execution (execute_sql is {execute_note})\n\n"
            "Available tools:\n"
            f"{tool_lines}\n\n"
            "Return JSON only. You may include memory_updates so the next round has compressed context:\n"
            '  {"action":"call_tool","tool":"retrieve_schema_context","args":{"request":"..."},"thought":"...",'
            '"result_assessment":"what the previous tool result showed and what you conclude from it",'
            '"memory_updates":{"verified":[],"findings":[],"hypotheses":[],"excluded_paths":[],"open_questions":[]},"next_action_hint":"..."}\n'
            '  {"action":"finish","answer":"markdown answer for the user","memory_updates":{"findings":[]}}\n\n'
            "Flow (each tool's own args/behaviour are in the list above — this is only the ordering):\n"
            "- Data query: retrieve_schema_context → (inspect/profile/exploratory execute_readonly_sql as "
            "needed) → retrieve_join_context if a join is needed → ask_user if a business choice is open → "
            "generate_sql → validate_sql → execute_sql → finish. Schema/where-is, profile, and SQL-explain "
            "questions skip ahead and finish earlier.\n"
            "- generate_sql uses every disclosed schema unless you pass `tables`; pass only the tables you "
            "deliberately chose. On a validate_sql schema error, inspect the objects and retry corrected SQL.\n"
            "- Call annotate_object only when the user states/confirms a durable fact (a timezone, a status "
            "meaning, a deprecation); never invent one. finish when you have enough to answer.\n"
            f"- {answer_language_directive(state.answer_language)}"
        )

    def user_prompt(self, state: Any, transcript: list[str]) -> str:
        history = _compact_history(transcript) if transcript else "(no recent raw tool calls)"
        pins = _pinned_scope_labels(getattr(self.orchestrator, "schema_scope", None))
        pin_line = (f"User-attached schema (prefer these; retrieve_schema_context on them directly, "
                    f"no broad discovery needed): {', '.join(pins)}\n\n") if pins else ""
        notes = decision_notes_block(self.orchestrator, state.database)
        notes_line = f"{notes}\n\n" if notes else ""
        confirmed = [c for c in self.orchestrator.run_state.clarifications if str(c).strip()]
        criteria_line = ""
        if confirmed:
            criteria_line = (
                "Confirmed criteria (already settled with the user — honour these, do NOT re-ask):\n"
                + "\n".join(f"- {c}" for c in confirmed) + "\n\n"
            )
        timezone = str(getattr(self.orchestrator.session.connection, "session_timezone", "UTC") or "UTC")
        today = date.today().isoformat()
        return (
            f"User question:\n{state.question}\n\n"
            f"Database scope: {state.database or '(any)'}\n\n"
            f"Today's date: {today} (resolve relative periods from this; if a scope is still "
            f"under-specified after using it, that is a business choice to confirm, not to assume)\n\n"
            f"Connection session timezone: {timezone}\n\n"
            f"Answer language for final user-facing prose: {state.answer_language}\n\n"
            f"{notes_line}"
            f"{criteria_line}"
            f"{pin_line}"
            f"Compressed working memory:\n{self.orchestrator.run_state.memory.prompt_block() or '(empty)'}\n\n"
            f"Recent raw tool results (only for extra detail; prefer memory):\n{history}"
        )


def tool_prompt_line(spec: Any) -> str:
    schema = getattr(spec, "input_schema", None) or {}
    if schema:
        args = ", ".join(f"{key}: {value}" for key, value in schema.items())
        return f"- {spec.name}(args: {{{args}}}): {spec.description}"
    return f"- {spec.name}(args: {{}}): {spec.description}"


def _pinned_scope_labels(scope: dict | None) -> list[str]:
    if not isinstance(scope, dict) or not scope:
        return []
    labels: list[str] = []
    for target in scope.get("tables") or []:
        db = str((target or {}).get("database") or "").strip()
        table = str((target or {}).get("table") or "").strip()
        if table:
            labels.append(f"{db}.{table}" if db else table)
    for db in scope.get("databases") or []:
        db = str(db or "").strip()
        if db:
            labels.append(f"{db}.*")
    return labels


def _compact_history(transcript: list[str]) -> str:
    items = [_shorten(item, RAW_HISTORY_ITEM_LIMIT) for item in transcript[-RAW_HISTORY_ITEMS:]]
    return "\n\n".join(items)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…[truncated]"
