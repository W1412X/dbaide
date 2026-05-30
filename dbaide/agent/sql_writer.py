from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo

logger = logging.getLogger("dbaide.sql_writer")


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
        table: str,
        columns: list[ColumnInfo],
        *,
        context: dict | None = None,
        feedback: str = "",
    ) -> SQLDraft:
        if isinstance(self.llm, NullLLMClient):
            raise ModelRequiredError("LLM is required for SQL generation.")
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
        if feedback.strip():
            user_prompt += f"\n\nPrevious SQL failed validation or execution. Fix it:\n{feedback.strip()}"
        payload = self.llm.complete_json(
            [
                LLMMessage("system", self._system_prompt()),
                LLMMessage("user", user_prompt),
            ],
            schema_hint='Return JSON only: {"sql": "...", "rationale": "...", "confidence": 0.0}.',
        )

        # Robust parsing - handle various output formats
        sql = self._extract_field(payload, "sql", str)
        rationale = self._extract_field(payload, "rationale", str, default="")
        confidence = self._extract_field(payload, "confidence", float, default=0.5)

        if not sql:
            raise ValueError("LLM returned empty SQL")

        return SQLDraft(sql=sql, rationale=rationale, confidence=confidence)

    def _extract_field(self, payload: dict, key: str, expected_type: type, default: Any = None) -> Any:
        """Robustly extract field from LLM output, handling format variations."""
        value = payload.get(key)

        # Handle missing key
        if value is None:
            if default is not None:
                return default
            raise KeyError(f"Missing required field: {key}")

        # Handle type conversion
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

    def _system_prompt(self) -> str:
        return (
            "You generate safe read-only SQL for a CLI database assistant. "
            "Use only disclosed tables and columns. Return one SELECT/WITH statement. "
            "Do not invent columns. Prefer simple SQL. "
            "Return confidence 0.0-1.0 based on how sure you are about the mapping."
        )

    def _user_prompt(self, question: str, table: str, columns: list[ColumnInfo], context: dict) -> str:
        col_lines = "\n".join(
            f"- {c.name}: {c.data_type}, pk={c.primary_key}, indexed={c.indexed}, comment={c.comment}"
            for c in columns
        )
        return f"Dialect: {self.dialect}\nQuestion: {question}\nTable: {table}\nColumns:\n{col_lines}\nContext: {context}"
