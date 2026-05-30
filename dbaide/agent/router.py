from __future__ import annotations

import logging

from dbaide.agent.progressive_schema import ModelRequiredError
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient
from dbaide.models import TaskType

logger = logging.getLogger("dbaide.router")


class TaskRouter:
    """LLM-only task routing — no keyword fallback."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or NullLLMClient()

    def route(self, text: str) -> TaskType:
        if isinstance(self.llm, NullLLMClient):
            raise ModelRequiredError("LLM is required for task routing.")
        try:
            return self._llm_classify(text)
        except ModelRequiredError:
            raise
        except Exception as exc:
            logger.error("llm_classify_failed: %s", exc)
            raise RuntimeError(f"LLM routing failed: {exc}") from exc

    def _llm_classify(self, text: str) -> TaskType:
        payload = self.llm.complete_json(
            [
                LLMMessage("system", self._system_prompt()),
                LLMMessage("user", text),
            ],
            schema_hint='Return {"task": "schema_explore|data_query|data_profile|sql_rewrite|sql_diagnose|db_compare|export|unknown"}.',
        )
        task_str = payload.get("task", "unknown")
        if isinstance(task_str, str):
            task_str = task_str.strip().lower()
            task_map = {
                "query": "data_query",
                "select": "data_query",
                "explore": "schema_explore",
                "schema": "schema_explore",
                "profile": "data_profile",
                "diagnose": "sql_diagnose",
                "explain": "sql_diagnose",
                "rewrite": "sql_rewrite",
                "compare": "db_compare",
            }
            task_str = task_map.get(task_str, task_str)
        try:
            return TaskType(task_str)
        except ValueError:
            logger.warning("unknown_task_type: %s", task_str)
            return TaskType.SCHEMA_EXPLORE

    def _system_prompt(self) -> str:
        return (
            "Classify a database assistant request.\n"
            "- schema_explore: find/describe tables, columns, schema, where data lives\n"
            "- data_query: counts, aggregates, lists, trends needing SQL\n"
            "- data_profile: data quality / distribution\n"
            "- sql_diagnose: explain/debug SQL\n"
            "- sql_rewrite: rewrite SQL\n"
            "- db_compare: compare schemas/databases\n"
            "- export: export data\n"
            "Return JSON only: {\"task\": \"category_name\"}"
        )
