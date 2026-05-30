from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo

logger = logging.getLogger("dbaide.sql_writer")

DisclosedSchema = tuple[str, str, list[ColumnInfo]]  # database, table, columns


@dataclass(slots=True)
class SQLDraft:
    sql: str
    rationale: str
    confidence: float = 0.5


class SQLWriter:
    """LLM-only SQL generation with validation-oriented output parsing."""

    def __init__(self, llm: LLMClient | None = None, *, dialect: str = "generic") -> None:
        self.llm = llm or NullLLMClient()
        self.dialect = dialect

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
        return self._complete_sql(user_prompt, feedback)

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
        return self._complete_sql(user_prompt, feedback, multi_table=True)

    def _complete_sql(self, user_prompt: str, feedback: str, *, multi_table: bool = False) -> SQLDraft:
        if feedback.strip():
            user_prompt += f"\n\nPrevious SQL failed validation or execution. Fix it:\n{feedback.strip()}"
        payload = self.llm.complete_json(
            [
                LLMMessage("system", self._system_prompt(multi_table=multi_table)),
                LLMMessage("user", user_prompt),
            ],
            schema_hint='Return JSON only: {"sql": "...", "rationale": "...", "confidence": 0.0}.',
        )

        sql = self._extract_field(payload, "sql", str)
        rationale = self._extract_field(payload, "rationale", str, default="")
        confidence = self._extract_field(payload, "confidence", float, default=0.5)

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

    def _system_prompt(self, *, multi_table: bool = False) -> str:
        base = (
            "You generate safe read-only SQL for a CLI database assistant. "
            "Use only disclosed tables and columns. Return one SELECT/WITH statement. "
            "Do not invent columns or tables. Prefer simple SQL. "
            "Return confidence 0.0-1.0 based on how sure you are about the mapping."
        )
        if multi_table:
            base += (
                " Multiple tables are disclosed; use JOIN only when the question requires it. "
                "Join only on columns listed above; do not invent tables, columns, or join conditions."
            )
        return base

    def _user_prompt(self, question: str, table: str, columns: list[ColumnInfo], context: dict) -> str:
        col_lines = self._format_columns(columns)
        blocks = [
            f"Dialect: {self.dialect}",
            f"Question: {question}",
            f"Table: {table}",
            f"Columns:\n{col_lines}",
        ]
        rel = self._format_relations(context)
        if rel:
            blocks.append(rel)
        blocks.append(f"Context: {self._prompt_context(context)}")
        return "\n".join(blocks)

    def _user_prompt_multi(self, question: str, disclosed_schemas: list[DisclosedSchema], context: dict) -> str:
        blocks: list[str] = [
            f"Dialect: {self.dialect}",
            f"Question: {question}",
            "Disclosed schemas (use ONLY these tables and columns):",
        ]
        for database, table, columns in disclosed_schemas:
            label = f"{database}.{table}" if database else table
            blocks.append(f"Table: {label}")
            blocks.append("Columns:")
            blocks.append(self._format_columns(columns))
        rel = self._format_relations(context)
        if rel:
            blocks.append(rel)
        blocks.append(f"Context: {self._prompt_context(context)}")
        return "\n".join(blocks)

    @staticmethod
    def _prompt_context(context: dict) -> dict:
        return {k: v for k, v in context.items() if k != "foreign_keys"}

    @staticmethod
    def _format_relations(context: dict) -> str:
        relations = context.get("foreign_keys")
        if not relations:
            return ""
        lines = ["Declared foreign keys (database facts; use only these for JOINs):"]
        for fk in relations:
            lines.append(
                f"- {fk.get('table')}.{fk.get('column')} -> {fk.get('ref_table')}.{fk.get('ref_column')}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_columns(columns: list[ColumnInfo]) -> str:
        return "\n".join(
            f"- {c.name}: {c.data_type}, pk={c.primary_key}, indexed={c.indexed}, comment={c.comment}"
            for c in columns
        )
