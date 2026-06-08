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

# Session-memory rendering: how many of the most-recent completed turns to
# summarise into the user prompt by default. Older turns are NOT silently
# truncated — the model can page through them via list_earlier_turns(offset=…).
PRIOR_TURNS_WINDOW = 3
PRIOR_TURN_ANSWER_CHARS = 160
PRIOR_TURN_SQL_CHARS = 160


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
            "• Keep global sight of the goal and the compressed working memory; it preserves key facts "
            "and raw evidence refs (mem:n, work-step/report/SQL-artifact ids). When the visible summary "
            "lacks a detail you already observed, call retrieve_memory_item(ref=...) instead of re-running "
            "the tool; don't call it when the summary is already enough.\n"
            "• Treat failed tool calls as observations, not as the end of the task. Read the error, "
            "then decide whether to use another tool, answer from existing evidence, or ask only "
            "for irreducible business intent.\n"
            "• Each round, briefly assess the previous tool's result in `result_assessment` (what it "
            "showed and what you conclude) — it is attached to that step so the work log reads "
            "did-what → result → judgment.\n"
            "• Maintain memory deliberately via memory_updates each round: put a conclusion you have "
            "VERIFIED with tool evidence or a user-confirmed fact in `verified` (it becomes settled "
            "context you should not re-investigate); put tentative observations in `findings`, guesses "
            "in `hypotheses`, ruled-out tables/columns/interpretations in `excluded_paths` (with a "
            "reason), and remaining unknowns in `open_questions`. This is how you keep track of what is "
            "confirmed vs. still open across rounds.\n"
            "• Use retrieve_schema_context first for data questions. It returns only schema evidence: "
            "candidate tables, user notes, inactive/missing paths, and columns. If it shows "
            "several plausible active tables/columns/grains whose choice changes the answer, ask_user "
            "with concrete options or inspect more evidence. Do not silently collapse candidates.\n"
            "• Join/relation evidence is a separate responsibility. After you have narrowed the relevant "
            "tables and the SQL needs a join, call retrieve_join_context with those table names. By default "
            "it reads only user-saved joins and declared FKs. Set infer_semantic=true or validate_sample=true "
            "only when you explicitly need that extra evidence; you still decide whether the relation matches "
            "the user's intent and grain.\n"
            "• User notes are authoritative. If a note says a table/column is deprecated, replaced, "
            "must not be used, has a timezone, or defines a status value, obey it and preserve that "
            "fact in memory.\n"
            "• SQL is an exploration tool as well as the final query. For intermediate evidence, call "
            "execute_readonly_sql with a clear purpose/save_as; the loop will continue so you can inspect "
            "counts, samples, consistency, or compare hypotheses. For the final answer query, call execute_sql.\n"
            "• For database metadata questions (checking exact table/column existence, checking column "
            "existence across tables, indexes, FKs, DDL-like structure), use inspect_metadata/list_tables/"
            "describe_table instead of querying information_schema or other system catalogs through SQL.\n"
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
            "To cut round-trips you MAY batch several INDEPENDENT read-only evidence calls in one "
            "decision; the loop runs them in order and you see all results next round:\n"
            '  {"action":"call_tools","calls":[{"tool":"describe_table","args":{"table":"orders"}},'
            '{"tool":"describe_table","args":{"table":"users"}}],"thought":"..."}\n'
            "Only batch tools that do NOT depend on each other's result: describe_table, column_stats, "
            "profile_table, retrieve_schema_context, inspect_metadata, retrieve_join_context, list_tables, "
            "retrieve_memory_item. NEVER batch the generate_sql → validate_sql → execute_sql chain (each "
            "needs the previous result and its safety gate), ask_user, or any write — use a single "
            "call_tool for those so the loop decides from each result.\n"
            "Tool guidance:\n"
            "- User-attached schema: when the user prompt contains a 'User-attached schema' line, those "
            "tables/databases are the user's explicit focus. Start by calling retrieve_schema_context on "
            "them directly — do NOT run a broad discover_schema first. Only broaden discovery if the "
            "pinned tables turn out insufficient for the question.\n"
            "- Schema / where-is questions: discover_schema or retrieve_schema_context → finish\n"
            "- Data queries: retrieve_schema_context, inspect/profile/run exploratory execute_readonly_sql "
            "as needed, call retrieve_join_context if joins are needed, ask_user if necessary, then "
            "generate_sql → validate_sql → execute_sql → finish\n"
            "- generate_sql uses all currently disclosed schemas unless you pass tables. If retrieve_schema_context returned many candidates, pass only the table names you intentionally chose; otherwise inspect or ask first.\n"
            "- Call validate_joins only when the user explicitly asks to re-check already loaded joins. Use list_joins only when the user asks to inspect saved joins.\n"
            "- If schema is ambiguous or multiple valid interpretations exist at any point, inspect more evidence before asking. Ask only when the remaining ambiguity is a business choice the database cannot answer. Ground options in actual candidate table names, column names, or observed values; never ask an open 'which field?' question when the candidates are known.\n"
            "- Anti-premature-clarification check (STRUCTURE/FACTS only, category (a)): when you feel like "
            "asking about something the schema or data could reveal, first ask whether another "
            "schema/profile/join/SQL tool call could answer it; if yes, call that tool instead of ask_user. "
            "This check does NOT apply to business-caliber choices (category (b)) — those are not "
            "discoverable and must be confirmed, not guessed.\n"
            "- ask_user pauses the run until the user replies; the next user message resumes the same workflow.\n"
            "- When validation reports invalid schema references, inspect the relevant objects and retry with corrected SQL.\n"
            "- If SQL fails because it tried to inspect system metadata, switch to inspect_metadata or describe_table.\n"
            "- describe_table is the lowest pre-built level (full structure + a small sample); there are no per-column docs. To learn a column's actual values (e.g. which status/flag value means what), use column_stats with metrics=[\"top_values\"].\n"
            "- You may repeat a tool call when recovery, verification, or a fresh context requires it; prefer retrieve_memory_item when prior evidence is enough.\n"
            "- Profile questions: discover_schema → describe_table → column_stats → finish\n"
            "- SQL explain: validate_sql or explain_sql as needed → finish\n"
            "- Prefer precision over listing everything.\n"
            "- When you have enough to answer, use action=finish.\n"
            "- Remember durable facts: when the user states or confirms a "
            "lasting fact about an object — a column's timezone/encoding, what a status value means, "
            "that a table is deprecated and which replaces it — call annotate_object to save it so "
            "future questions benefit. Only save what the user actually stated; never invent a note.\n"
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
        prior_turns_block = _prior_turns_block(self.orchestrator)
        prior_turns_line = f"{prior_turns_block}\n\n" if prior_turns_block else ""
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
            f"{prior_turns_line}"
            f"Compressed working memory:\n{self.orchestrator.run_state.memory.prompt_block() or '(empty)'}\n\n"
            f"Recent raw tool results (only for extra detail; prefer memory):\n{history}"
        )


def tool_prompt_line(spec: Any) -> str:
    schema = getattr(spec, "input_schema", None) or {}
    if schema:
        args = ", ".join(f"{key}: {value}" for key, value in schema.items())
        return f"- {spec.name}(args: {{{args}}}): {spec.description}"
    return f"- {spec.name}(args: {{}}): {spec.description}"


def _prior_turns_block(orchestrator: Any) -> str:
    """Render the [Prior turns in this session] section: a thin window of the
    most-recent completed turns (question + answer + selected SQL) so a follow-up
    question can attach to the previous one. The model invokes retrieve_turn or
    list_earlier_turns when it needs more than this summary — same progressive-
    disclosure pattern as schema and join evidence."""
    turns = list(getattr(orchestrator, "session_turns", []) or [])
    if not turns:
        return ""
    window = turns[-PRIOR_TURNS_WINDOW:]
    earlier = len(turns) - len(window)
    lines = [f"[Prior turns in this session]  (showing {len(window)} of {len(turns)}; "
             f"use retrieve_turn(turn_id) for clarifications/full SQL/full answer, "
             f"list_earlier_turns(offset=0) for older turns)"]
    base_index = earlier  # so the most recent gets the highest tN id
    for i, turn in enumerate(window):
        idx = base_index + i + 1
        turn_id = f"t{idx}"
        question = _shorten(str(turn.get("question") or ""), 200)
        answer = _shorten(str(turn.get("answer_markdown") or "").replace("\n", " "),
                          PRIOR_TURN_ANSWER_CHARS)
        sql = _shorten(str(turn.get("selected_sql") or "").replace("\n", " "),
                       PRIOR_TURN_SQL_CHARS)
        lines.append(f"- {turn_id}: Q: {question or '(empty)'}")
        if answer:
            lines.append(f"     A: {answer}")
        if sql:
            lines.append(f"     SQL: {sql}")
    if earlier > 0:
        lines.append(f"(+{earlier} earlier turn(s) — list_earlier_turns(offset=0, limit=…) to page back)")
    return "\n".join(lines)


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
