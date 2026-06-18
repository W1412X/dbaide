from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from dbaide.agent.join_validation import _safe_confidence
from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.i18n import answer_language_directive
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo

logger = logging.getLogger("dbaide.sql_writer")

DisclosedSchema = tuple[str, str, list[ColumnInfo]]  # database, table, columns


def _normalize_confidence(value: Any) -> float:
    """Coerce an LLM confidence into a 0–1 fraction.

    The prompt asks for 0.0–1.0, but models routinely return a 0–100 percentage
    instead (e.g. ``50`` for 50%). Left unnormalized, ``50 < 0.8`` is False, so a
    merely-medium-confidence draft would slip past the fast-execute gate as if it
    were near-certain. Treat any value > 1 as a percentage and clamp to [0, 1];
    ambiguous values resolve toward LOWER confidence (the safe direction — more
    verification, never a spurious auto-execute)."""
    c = _safe_confidence(value)
    if c > 1.0:
        c = c / 100.0
    return max(0.0, min(1.0, c))


@dataclass(slots=True)
class SQLDraft:
    sql: str
    rationale: str
    confidence: float = 0.5


class SQLWriter:
    """LLM-only SQL generation with validation-oriented output parsing."""

    def __init__(self, llm: LLMClient | None = None, *, dialect: str = "generic",
                 server_version: str = "", session_timezone: str = "UTC") -> None:
        self.llm = llm or NullLLMClient()
        self.dialect = dialect
        self.server_version = str(server_version or "")
        self.session_timezone = str(session_timezone or "UTC")

    def write(
        self,
        question: str,
        table: str = "",
        columns: list[ColumnInfo] | None = None,
        *,
        disclosed_schemas: list[DisclosedSchema] | None = None,
        context: dict | None = None,
        feedback: str = "",
    ) -> SQLDraft:
        if isinstance(self.llm, NullLLMClient):
            raise ModelRequiredError("LLM is required for SQL generation.")
        if disclosed_schemas:
            return self._llm_write_disclosed(question, disclosed_schemas, context or {}, feedback)
        if not table or columns is None:
            raise ValueError("table and columns are required when disclosed_schemas is empty")
        return self._llm_write(question, table, columns, context or {}, feedback)

    def _llm_write(
        self,
        question: str,
        table: str,
        columns: list[ColumnInfo],
        context: dict,
        feedback: str,
    ) -> SQLDraft:
        """LLM-based SQL generation with robust output parsing."""
        user_prompt = self._user_prompt(question, table, columns, context)
        return self._complete_sql(user_prompt, feedback, language=str(context.get("answer_language") or ""))

    def _llm_write_disclosed(
        self,
        question: str,
        disclosed_schemas: list[DisclosedSchema],
        context: dict,
        feedback: str,
    ) -> SQLDraft:
        if len(disclosed_schemas) == 1:
            db, table, columns = disclosed_schemas[0]
            return self._llm_write(question, table, columns, context, feedback)
        user_prompt = self._user_prompt_multi(question, disclosed_schemas, context)
        return self._complete_sql(
            user_prompt,
            feedback,
            multi_table=True,
            language=str(context.get("answer_language") or ""),
        )

    def _complete_sql(self, user_prompt: str, feedback: str, *, multi_table: bool = False,
                      language: str = "") -> SQLDraft:
        if feedback.strip():
            user_prompt += f"\n\nPrevious SQL failed validation or execution. Fix it:\n{feedback.strip()}"
        payload = self.llm.complete_json(
            [
                LLMMessage("system", self._system_prompt(multi_table=multi_table, language=language)),
                LLMMessage("user", user_prompt),
            ],
            schema_hint='Return JSON only: {"sql": "...", "rationale": "...", "confidence": 0.0}.',
        )

        sql = self._extract_field(payload, "sql", str)
        rationale = self._extract_field(payload, "rationale", str, default="")
        confidence = _normalize_confidence(
            self._extract_field(payload, "confidence", float, default=0.5)
        )

        if not sql:
            raise ValueError("LLM returned empty SQL")

        return SQLDraft(sql=sql, rationale=rationale, confidence=confidence)

    def _extract_field(self, payload: dict, key: str, expected_type: type, default: Any = None) -> Any:
        """Robustly extract field from LLM output, handling format variations."""
        value = payload.get(key)

        if value is None:
            if default is not None:
                return default
            raise KeyError(f"Missing required field: {key}")

        try:
            if expected_type == str:
                return str(value).strip()
            elif expected_type == float:
                return float(value)
            elif expected_type == int:
                return int(value)
            else:
                return value
        except (ValueError, TypeError) as exc:
            logger.warning("type_conversion_failed: key=%s, value=%s, error=%s", key, value, exc)
            if default is not None:
                return default
            raise

    def _system_prompt(self, *, multi_table: bool = False, language: str = "") -> str:
        base = (
            "You generate safe read-only SQL for a CLI database assistant. "
            "Use only disclosed tables and columns. Return one SELECT/WITH statement. "
            "Do not invent columns or tables. Prefer simple SQL. "
            "Reference tables by their BARE name (e.g. `orders`, never `mydb.orders`) — the "
            "connection is already pointed at the correct database. Keep a disclosed schema qualifier "
            "such as `public.orders` when the table label includes it, but do not add a database prefix "
            "unless the query genuinely spans MORE THAN ONE database. "
            "Return confidence 0.0-1.0 based on how sure you are about the mapping. "
            "User notes are AUTHORITATIVE: they override DB comments and any inference — "
            "if a note says an object is deprecated, replaced, or must not be used, do NOT use it. "
            + self._dialect_rules()
            + answer_language_directive(language or None)
        )
        if multi_table:
            base += (
                " Multiple tables are disclosed; use JOIN when needed. "
                "Join hints are ranked by confidence — prefer higher-confidence edges; "
                "low-confidence joins may need casts or manual verification."
            )
        return base

    def _dialect_rules(self) -> str:
        version = f" Server version: {self.server_version}." if self.server_version else ""
        tz = (
            f" Connection session time zone: {self.session_timezone or 'unknown'}; use it for SQL runtime "
            "semantics such as NOW(), CURRENT_DATE, TIMESTAMP display/conversion and date truncation, "
            "but do not assume it is the business timezone of every stored column."
        )
        if self.dialect == "mysql":
            return (
                f" Target SQL dialect: MySQL/MariaDB.{version}{tz} "
                "Use only syntax supported by MySQL/MariaDB. MySQL does NOT support "
                "FULL OUTER JOIN; emulate it with LEFT JOIN UNION/UNION ALL RIGHT JOIN "
                "and an anti-duplicate WHERE clause. Do not output unsupported standard "
                "SQL and assume the execution layer will rewrite it. "
            )
        if self.dialect == "postgres":
            return f" Target SQL dialect: PostgreSQL.{version}{tz} Use PostgreSQL-compatible syntax. "
        if self.dialect == "sqlite":
            return f" Target SQL dialect: SQLite.{version}{tz} Use SQLite-compatible syntax. "
        return f" Target SQL dialect: {self.dialect or 'generic'}.{version}{tz} "

    def _user_prompt(self, question: str, table: str, columns: list[ColumnInfo], context: dict) -> str:
        col_lines = self._format_columns(columns)
        blocks = [
            f"Dialect: {self.dialect}",
            f"Server version: {self.server_version or 'unknown'}",
            f"Connection session time zone: {self.session_timezone or 'unknown'}",
            f"Question: {question}",
        ]
        notes = self._format_object_notes(context)
        if notes:
            blocks.append(notes)
        blocks += [
            f"Table: {self._format_table_label(table)}",
            f"Columns:\n{col_lines}",
        ]
        indexes = self._format_indexes(context)
        if indexes:
            blocks.append(indexes)
        rel = self._format_relations(context)
        if rel:
            blocks.append(rel)
        crit = self._format_criteria(context)
        if crit:
            blocks.append(crit)
        blocks.append(f"Context: {self._prompt_context(context)}")
        return "\n".join(blocks)

    def _user_prompt_multi(self, question: str, disclosed_schemas: list[DisclosedSchema], context: dict) -> str:
        # Only qualify tables with the database when the query truly spans more than
        # one — otherwise bare names (the connection is set to that database). A stray
        # `db.table` prefix is a common cause of "unknown table" at validation.
        distinct_dbs = {db for db, _, _ in disclosed_schemas if db}
        cross_db = len(distinct_dbs) > 1
        blocks: list[str] = [
            f"Dialect: {self.dialect}",
            f"Server version: {self.server_version or 'unknown'}",
            f"Connection session time zone: {self.session_timezone or 'unknown'}",
            f"Question: {question}",
        ]
        notes = self._format_object_notes(context)
        if notes:
            blocks.append(notes)
        if len(distinct_dbs) == 1:
            blocks.append(
                f"Active database: {next(iter(distinct_dbs))} "
                "(do not add this database as a prefix; keep schema-qualified table labels if shown)"
            )
        blocks.append("Disclosed schemas (use ONLY these tables and columns):")
        for database, table, columns in disclosed_schemas:
            table_label = self._format_table_label(table)
            label = f"{self._format_table_label(database)}.{table_label}" if (cross_db and database) else table_label
            blocks.append(f"Table: {label}")
            blocks.append("Columns:")
            blocks.append(self._format_columns(columns))
        indexes = self._format_indexes(context)
        if indexes:
            blocks.append(indexes)
        rel = self._format_relations(context)
        if rel:
            blocks.append(rel)
        crit = self._format_criteria(context)
        if crit:
            blocks.append(crit)
        blocks.append(f"Context: {self._prompt_context(context)}")
        return "\n".join(blocks)

    @staticmethod
    def _prompt_context(context: dict) -> dict:
        # `tables` is the full disclosure dump. The prompt already lists the exact
        # schemas passed to SQL generation, so re-dumping the full set here can
        # re-introduce irrelevant evidence the main loop did not select for SQL.
        skip = ("foreign_keys", "criteria", "indexes", "object_notes", "tables")
        return {k: v for k, v in context.items() if k not in skip}

    def _format_table_label(self, name: str) -> str:
        text = str(name or "").strip()
        if not text:
            return text
        if "." in text:
            from dbaide.adapters.base import quote_identifier
            return quote_identifier(text, self.dialect)
        if text.replace("_", "").isalnum() and not text[0].isdigit():
            return text
        quote = "`" if self.dialect in {"mysql", "mariadb"} else '"'
        escaped = text.replace(quote, quote + quote)
        return f"{quote}{escaped}{quote}"

    @staticmethod
    def _format_object_notes(context: dict) -> str:
        """Authoritative user notes on databases/tables — highest priority, top of prompt."""
        notes = [n for n in (context.get("object_notes") or []) if str(n.get("note") or "").strip()]
        if not notes:
            return ""
        lines = [
            "User notes (AUTHORITATIVE — override DB comments, schema guesses and any "
            "inference; follow what each note says, e.g. if it says an object is "
            "deprecated, replaced, or must not be used, do not use it):"
        ]
        for n in notes:
            lines.append(f"- {n.get('scope')} {n.get('label')}: {str(n.get('note')).strip()}")
        return "\n".join(lines)

    @staticmethod
    def _format_criteria(context: dict) -> str:
        """Confirmed business criteria (口径) — authoritative; the SQL MUST honour these."""
        criteria = [str(c).strip() for c in (context.get("criteria") or []) if str(c).strip()]
        if not criteria:
            return ""
        lines = ["Business criteria — apply these EXACTLY (timezone, definitions, NULL/filter handling):"]
        lines += [f"- {c}" for c in criteria]
        return "\n".join(lines)

    @staticmethod
    def _format_relation_line(fk: dict[str, Any]) -> str:
        base = f"- {fk.get('table')}.{fk.get('column')} -> {fk.get('ref_table')}.{fk.get('ref_column')}"
        join_type = str(fk.get("join_type") or "").strip()
        validation = fk.get("validation") if isinstance(fk.get("validation"), dict) else {}
        conf = fk.get("confidence")
        if conf is None:
            conf = validation.get("confidence")
        tags: list[str] = []
        if conf is not None:
            try:
                tags.append(f"conf={float(conf):.0%}")
            except (TypeError, ValueError):
                pass
        if join_type and join_type != "unknown":
            tags.append(join_type)
        match_rate = validation.get("match_rate")
        if match_rate is not None:
            try:
                if float(match_rate) > 0:
                    tags.append(f"match={float(match_rate):.0%}")
            except (TypeError, ValueError):
                pass
        reason = str(fk.get("reason") or validation.get("message") or "").strip()
        suffix = f" [{', '.join(tags)}]" if tags else ""
        if reason:
            suffix += f" — {reason[:120]}"
        return base + suffix

    @staticmethod
    def _format_relations(context: dict) -> str:
        relations = sorted(
            list(context.get("foreign_keys") or []),
            key=lambda r: _safe_confidence(r.get("confidence")),
            reverse=True,
        )
        if not relations:
            return ""
        declared = [fk for fk in relations if fk.get("source") != "semantic"]
        semantic = [fk for fk in relations if fk.get("source") == "semantic"]
        blocks: list[str] = []
        if declared:
            lines = ["Declared foreign keys (schema facts; highest priority):"]
            lines.extend(SQLWriter._format_relation_line(fk) for fk in declared)
            blocks.append("\n".join(lines))
        if semantic:
            lines = ["Semantic join hints (LLM + sample confidence; use when helpful):"]
            lines.extend(SQLWriter._format_relation_line(fk) for fk in semantic)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    @staticmethod
    def _format_indexes(context: dict) -> str:
        entries = context.get("indexes") or []
        if not isinstance(entries, list):
            return ""
        lines = ["Indexes (complete definitions; composite indexes keep column order):"]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            table = str(entry.get("table") or "").strip()
            database = str(entry.get("database") or "").strip()
            label = f"{database}.{table}" if database else table
            for raw in entry.get("indexes") or []:
                if not isinstance(raw, dict):
                    continue
                columns = raw.get("columns") or []
                if not isinstance(columns, list):
                    columns = [columns]
                col_text = ", ".join(str(c) for c in columns)
                attrs: list[str] = []
                if raw.get("unique"):
                    attrs.append("unique")
                if raw.get("primary"):
                    attrs.append("primary")
                if raw.get("type"):
                    attrs.append(f"type={raw.get('type')}")
                suffix = f" [{', '.join(attrs)}]" if attrs else ""
                name = str(raw.get("name") or "(unnamed)").strip()
                lines.append(f"- {label}.{name}: ({col_text}){suffix}")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _format_columns(columns: list[ColumnInfo]) -> str:
        def _line(c: ColumnInfo) -> str:
            base = f"- {c.name}: {c.data_type}, pk={c.primary_key}, comment={c.comment}"
            note = str(getattr(c, "note", "") or "").strip()
            if note:
                base += f", note(AUTHORITATIVE)={note}"
            return base

        return "\n".join(_line(c) for c in columns)
