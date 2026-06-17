"""Prompt construction for the Ask agent loop (conversation-stream architecture).

The system prompt is sent once as messages[0]. The initial user message (question,
context, prior turns) is messages[1]. All subsequent rounds append assistant/user
messages to the growing conversation — the model sees its own prior decisions and
full tool results directly.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from dbaide.agent.schema_context import decision_notes_block
from dbaide.i18n import answer_language_directive


_DEFAULT_PRIOR_TURNS_WINDOW = 3
PRIOR_TURN_ANSWER_CHARS = 300
PRIOR_TURN_SQL_CHARS = 300


def estimate_tokens(text: str) -> int:
    """Rough token estimate that accounts for CJK content.

    CJK characters are ~1.5 tokens each on average (often 1-2 tokens per char).
    ASCII/Latin content is ~4 chars per token. JSON/code overhead sits in between.
    We scan for CJK ranges and weight accordingly rather than using a flat ratio.
    """
    if not text:
        return 1
    cjk = 0
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
                or 0xF900 <= cp <= 0xFAFF or 0x2E80 <= cp <= 0x2EFF
                or 0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF):
            cjk += 1
    ascii_chars = len(text) - cjk
    return max(1, int(cjk * 1.5 + ascii_chars / 4))


class DecisionPromptBuilder:
    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def system_prompt(self, state: Any, tool_lines: str, execute_note: str) -> str:
        lang_directive = answer_language_directive(state.answer_language)
        prefetched = getattr(self.orchestrator.run_state, "schema_prefetched", False)
        prefetch_hint = (
            "Schema evidence has been pre-fetched and is visible in the conversation. "
            "Use it directly — do NOT call retrieve_schema_context or discover_schema again "
            "unless the evidence is insufficient.\n"
        ) if prefetched else ""
        return (
            "<role>\n"
            "You are DBAide, a database assistant operating in a tool loop.\n"
            "You are the only decision-making brain. Tools collect evidence; they do not decide "
            "which schema, metric, filter, or final answer is correct. Think, act, incorporate the "
            "result, then choose the next step until the user's single intent is solved.\n"
            "</role>\n\n"

            "<context>\n"
            "This conversation is a continuous stream. You see your own prior decisions and full "
            "tool results as earlier messages. Use this context directly — no need to retrieve "
            "earlier results. When the context grows large, older messages may be replaced by a "
            "compressed summary; rely on the summary for historical context.\n"
            "</context>\n\n"

            "<rules>\n"
            "<errors>\n"
            "Treat failed tool calls as observations, not as the end of the task. Read the error, "
            "then decide whether to use another tool, answer from existing evidence, or ask for "
            "irreducible business intent.\n"
            "</errors>\n\n"

            "<schema-discovery>\n"
            "Use retrieve_schema_context first for data questions. It returns schema evidence: "
            "candidate tables, user notes, inactive/missing paths, and columns. If it shows "
            "several plausible candidates whose choice changes the answer, ask_user "
            "with concrete options or inspect more evidence. Do not silently collapse candidates.\n"
            "User-attached schema: when the user prompt contains a 'User-attached schema' line, "
            "start by calling retrieve_schema_context on them directly — do NOT run a broad "
            "discover_schema first. Broaden only if pinned tables are insufficient.\n"
            f"{prefetch_hint}"
            "</schema-discovery>\n\n"

            "<joins>\n"
            "After narrowing candidate tables, if SQL needs a join, call retrieve_join_context. "
            "By default it reads only user-saved joins and declared FKs. Set infer_semantic=true "
            "or validate_sample=true only when you explicitly need extra evidence.\n"
            "</joins>\n\n"

            "<user-notes>\n"
            "User notes are authoritative. If a note says a table/column is deprecated, replaced, "
            "has a timezone, or defines a status value, obey it and preserve that fact.\n"
            "</user-notes>\n\n"

            "<sql-execution>\n"
            "Call execute_sql whenever you need database rows. Always pass purpose (≤20 chars, "
            "user language). Set exploratory=true for intermediate evidence-gathering queries "
            "(the run's final query_result is not updated); omit for the answer query.\n"
            "Use save_as when you will reuse the result (charts, later SQL). "
            "If execute_sql times out, treat the tool feedback as a plan-rewrite instruction: "
            "do NOT retry the same SQL or merely raise timeout. Write a faster SQL: reduce scanned "
            "rows, push filters earlier, use available indexes, aggregate before joining large "
            "tables, or use EXISTS/key-set checks, bounded probes, and sampled validation.\n"
            "No SQL tool ends the run — call action=finish only when the intent is fully answered.\n"
            "</sql-execution>\n\n"

            "<subagents>\n"
            "Use run_subagent only for separable, bounded subtasks where an independent "
            "DBAide agent should gather evidence or verify a claim while you remain the "
            "main decision maker. Do not delegate simple one-tool lookups. Treat the child "
            "answer as evidence: incorporate it, resolve conflicts, and finish yourself.\n"
            "</subagents>\n\n"

            "<metadata>\n"
            "For database metadata questions (table/column existence, indexes, FKs, DDL-like "
            "structure), use inspect_metadata/list_tables/describe_table instead of querying "
            "information_schema through SQL.\n"
            "</metadata>\n\n"

            "<clarification>\n"
            "Separate what the DATA CAN REVEAL from what only the USER'S INTENT CAN DECIDE.\n"
            "(a) FACTS the database can reveal — anything determinable from schema, data, or an "
            "authoritative user note. NEVER ask the user for these; discover them with tools.\n"
            "(b) INTENT the data cannot decide — what the question MEANS, not what the database "
            "contains. When the question text, today's date, the schema, the data, and user "
            "notes cannot tell which interpretation the user means, resolve it with ask_user "
            "— offering concrete interpretations as options — BEFORE generating SQL.\n"
            "Resolve everything discoverable first, then ask ONE consolidated question covering "
            "only the genuinely undecidable choices. Honour Confirmed criteria; never re-ask.\n"
            "</clarification>\n\n"

            "<no-invention>\n"
            "Do not invent tables, columns, SQL features, status meanings, units, or timezones. "
            f"The connection session timezone is configured in the connection; SQL writer also sees it.\n"
            "</no-invention>\n"
            "</rules>\n\n"

            f"Execution mode: guarded read-only execution (execute_sql is {execute_note})\n\n"

            "<tools>\n"
            f"{tool_lines}\n"
            "</tools>\n\n"

            "<response-format>\n"
            "Return a single JSON object each round. Examples:\n"
            '  {"action":"call_tool","tool":"...","args":{...},"thought":"..."}\n'
            '  {"action":"finish","answer":"markdown answer for the user"}\n'
            "Optional fields:\n"
            '  "memory_updates":{"verified":["fact"],"excluded_paths":[{"target":"t","reason":"r"}]}\n'
            "memory_updates.verified: facts confirmed with tool evidence (carried across runs).\n"
            "memory_updates.excluded_paths: ruled-out tables/columns/interpretations.\n"
            "</response-format>\n\n"

            "<batching>\n"
            "You MAY batch several INDEPENDENT read-only evidence calls:\n"
            '  {"action":"call_tools","calls":[{"tool":"describe_table","args":{"table":"orders"}},'
            '{"tool":"describe_table","args":{"table":"users"}}],"thought":"..."}\n'
            "Batchable: describe_table, column_stats, profile_table, retrieve_schema_context, "
            "inspect_metadata, retrieve_join_context, list_tables.\n"
            "NEVER batch: generate_sql → validate_sql → execute_sql (each needs the previous "
            "result), ask_user, or any write.\n"
            "</batching>\n\n"

            "<tool-guidance>\n"
            "- Data queries: retrieve_schema_context → inspect/profile as needed → "
            "retrieve_join_context if joins needed → generate_sql → validate_sql → execute_sql → "
            "(render_chart if needed) → finish\n"
            "- generate_sql may auto-validate and auto-execute when conditions are met "
            "(single table, high confidence, low risk). Check the response for "
            "'fast_executed: true' — if present, the query already ran and the result is "
            "included; proceed directly to finish with the data.\n"
            "- Schema questions: discover_schema or retrieve_schema_context → finish\n"
            "- Profile questions: discover_schema → describe_table → column_stats/profile_table → finish\n"
            "- SQL explain: validate_sql or explain_sql → finish\n"
            "- Loop termination: ONLY action=finish ends the run (or ask_user pauses). "
            "No tool auto-completes the task.\n"
            "- generate_sql uses all disclosed schemas unless you pass tables.\n"
            "- When validation reports invalid schema references, inspect and retry with corrected SQL.\n"
            "- describe_table is the lowest pre-built level; for column values, use column_stats(metrics=[\"top_values\"]).\n"
            "- Charts: call render_chart after execute_sql. Split charts when measures differ in "
            "unit/scale/meaning. Embed with {{chart:N}} in your finish answer.\n"
            "- Subagents: call run_subagent for independent research/verification subtasks; "
            "use the returned answer/SQL/preview as evidence, then continue or finish.\n"
            "- Annotations: when the user states a durable fact about an object, call annotate_object.\n"
            f"- {lang_directive}\n"
            "</tool-guidance>"
        )

    def initial_user_prompt(self, state: Any) -> str:
        """Build the initial user message — called once at conversation start."""
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
        session = self.orchestrator.session
        prior_window = getattr(session, "prior_turns_window", _DEFAULT_PRIOR_TURNS_WINDOW)
        prior_turns_block = _prior_turns_block(self.orchestrator, window_size=prior_window)
        prior_turns_line = f"{prior_turns_block}\n\n" if prior_turns_block else ""

        mem = self.orchestrator.run_state.memory
        verified_line = ""
        if mem.verified_facts:
            verified_line = (
                "Verified facts (confirmed with tool evidence in prior turns — trust these):\n"
                + "\n".join(f"- {f}" for f in mem.verified_facts) + "\n\n"
            )
        excluded_line = ""
        if mem.excluded_paths:
            excluded_line = (
                "Excluded paths (ruled out — do NOT retry these):\n"
                + "\n".join(f"- {e.target}: {e.reason}" for e in mem.excluded_paths) + "\n\n"
            )

        return (
            f"User question:\n{state.question}\n\n"
            f"Database scope: {state.database or '(any)'}\n\n"
            f"Today's date: {today} (resolve relative periods from this; if a scope is still "
            f"under-specified after using it, that is a business choice to confirm, not to assume)\n\n"
            f"Connection session timezone: {timezone}\n\n"
            f"Answer language for final user-facing prose: {state.answer_language}\n\n"
            f"{notes_line}"
            f"{criteria_line}"
            f"{verified_line}"
            f"{excluded_line}"
            f"{pin_line}"
            f"{prior_turns_line}"
        ).rstrip() + "\n"


def tool_prompt_line(spec: Any) -> str:
    """Render a tool spec as a compact prompt line with required/optional markers."""
    schema = getattr(spec, "input_schema", None) or {}
    output = getattr(spec, "output_schema", None) or {}

    if schema:
        required_parts: list[str] = []
        optional_parts: list[str] = []
        for key, meta in schema.items():
            if isinstance(meta, dict):
                is_req = meta.get("required", False)
                default = meta.get("default")
                desc = meta.get("description", "")
                if default is not None:
                    optional_parts.append(f"{key}={default}")
                elif is_req:
                    required_parts.append(key)
                else:
                    optional_parts.append(f"{key}?")
                if desc and len(desc) <= 60:
                    target = required_parts if is_req else optional_parts
                    idx = len(target) - 1
                    if idx >= 0:
                        target[idx] += f"  /*{desc}*/"
            else:
                optional_parts.append(f"{key}?")
        params = ", ".join(required_parts + optional_parts)
    else:
        params = ""

    line = f"- {spec.name}({params}): {spec.description}"
    if output:
        fields = ", ".join(output.keys())
        line += f"\n  → {{{fields}}}"
    return line


def _prior_turns_block(orchestrator: Any, *, window_size: int = _DEFAULT_PRIOR_TURNS_WINDOW) -> str:
    """Render the [Prior turns in this session] section."""
    turns = list(getattr(orchestrator, "session_turns", []) or [])
    if not turns:
        return ""
    window = turns[-window_size:]
    earlier = len(turns) - len(window)
    lines = [f"[Prior turns in this session]  (showing {len(window)} of {len(turns)}; "
             f"use retrieve_turn(turn_id) for clarifications/full SQL/full answer, "
             f"list_earlier_turns(offset=0) for older turns)"]
    base_index = earlier
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


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…[truncated]"
