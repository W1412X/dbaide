"""LLM test doubles for agent/schema tests."""

from __future__ import annotations

import re
from typing import Any

from dbaide.llm import LLMClient, LLMMessage


class AgentMockLLM(LLMClient):
    """Responds based on system-prompt role (route / filter / sql / synthesize)."""

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict[str, Any]:
        system = messages[0].content if messages else ""
        user = messages[-1].content
        if "tool loop" in system.lower():
            return self._loop_decision(messages)
        if "Classify a database assistant" in system:
            return self._route(user)
        if "relevant_indices" in system:   # the schema-discovery shortlist/filter step
            return self._filter(user)
        if "generate safe read-only SQL" in system:
            return self._sql(user)
        return {}

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "OK"

    def _route(self, user: str) -> dict[str, Any]:
        if any(k in user for k in ("数据质量", "profile", "空值率")):
            return {"task": "data_profile"}
        if any(k in user for k in ("explain", "为什么这么慢", "执行计划")):
            return {"task": "sql_diagnose"}
        if any(k in user for k in ("有哪些表", "表结构", "schema", "字段", "在哪里", "产线")):
            return {"task": "schema_explore"}
        if "改写" in user and "SQL" in user:
            return {"task": "sql_rewrite"}
        if any(k in user for k in ("查", "统计", "select", "订单", "数量")):
            return {"task": "data_query"}
        return {"task": "unknown"}

    def _filter(self, user: str) -> dict[str, Any]:
        question = _extract_question(user)
        indices: list[int] = []
        objects_section = user.split("Objects:\n")[-1] if "Objects:" in user else user
        matches = list(re.finditer(r"\[(\d+)\]\s+([^\n—]+)", objects_section))
        for match in matches:
            idx = int(match.group(1))
            name = match.group(2).strip().split(" (")[0]
            if _object_relevant(name, question, user):
                indices.append(idx)
        if not indices and matches and "table=" not in user:
            indices = [int(m.group(1)) for m in matches]
        return {"relevant_indices": indices, "reason": "mock filter"}

    def _sql(self, user: str) -> dict[str, Any]:
        if "orders" in user.lower():
            return {
                "sql": (
                    "SELECT DATE(created_at) AS day, COUNT(*) AS row_count FROM orders "
                    "WHERE created_at >= DATE('now', '-7 day') GROUP BY day ORDER BY day"
                ),
                "rationale": "Daily order counts for the last 7 days.",
                "confidence": 0.9,
            }
        return {"sql": "SELECT 1", "rationale": "test", "confidence": 0.5}

    def _synthesize(self, user: str) -> str:
        if "email" in user.lower():
            return "The `email` column is in **local.main.users** (`local.main.users.email`)."
        if "产线" in user:
            return (
                "与产线相关的表主要在 `test_db_industrial_monitoring`：\n"
                "- **production_lines** — 产线主数据\n"
                "- **assets** — 设备关联 `line_id`\n"
                "- **quality_checks** — 产线质检"
            )
        return "Relevant schema listed above."

    def _loop_decision(self, messages: list[LLMMessage]) -> dict[str, Any]:
        system = messages[0].content if messages else ""
        user = messages[-1].content if messages else ""
        question = _extract_loop_question(user)
        prior = _count_completed_steps(user)
        execute_allowed = "execute_sql is allowed" in system

        if any(k in question for k in ("产线", "有哪些表", "在哪里", "schema", "字段", "email")):
            if prior == 0:
                return {
                    "action": "call_tool",
                    "tool": "retrieve_schema_context",
                    "args": {"request": question},
                    "thought": "Retrieve relevant schema evidence",
                }
            return {"action": "finish", "answer": self._synthesize(user)}

        if any(k in question for k in ("订单", "统计", "查")):
            if prior == 0:
                return {
                    "action": "call_tool",
                    "tool": "discover_schema",
                    "args": {"question": question},
                    "thought": "Find relevant tables",
                }
            if prior == 1:
                return {
                    "action": "call_tool",
                    "tool": "describe_table",
                    "args": {"table": "orders", "database": ""},
                    "thought": "Describe orders table",
                }
            if prior == 2:
                return {
                    "action": "call_tool",
                    "tool": "generate_sql",
                    "args": {"question": question, "table": "orders", "database": ""},
                    "thought": "Generate SQL",
                }
            if prior == 3:
                return {"action": "call_tool", "tool": "validate_sql", "args": {}, "thought": "Validate SQL"}
            if prior == 4 and execute_allowed:
                return {"action": "call_tool", "tool": "execute_sql", "args": {}, "thought": "Execute SQL"}
            return {"action": "finish", "answer": "Query complete."}

        return {"action": "finish", "answer": "No matching loop scenario in mock."}


def _extract_loop_question(user: str) -> str:
    if "User question:" in user:
        block = user.split("User question:", 1)[1]
        if "Database scope:" in block:
            block = block.split("Database scope:", 1)[0]
        return block.strip()
    return user


def _extract_question(user: str) -> str:
    if "Question:" not in user:
        return user
    block = user.split("Question:", 1)[1]
    for marker in ("\nContext:", "\n\nObjects:"):
        if marker in block:
            block = block.split(marker, 1)[0]
    return block.strip()


def _count_completed_steps(user: str) -> int:
    """Count completed work steps from the compressed working memory.

    The memory's [Work Done] section lists lines like:
        - w1 describe_table [ok] → ...
        - w2 generate_sql [ok] → ...
    """
    count = 0
    for line in user.split("\n"):
        if re.match(r"^\s*- w\d+ \w+", line):
            count += 1
    return count


def _object_relevant(name: str, question: str, context: str = "") -> bool:
    nl = name.lower()
    ql = question.lower()
    if "email" in ql or "邮箱" in question:
        if any(m in nl for m in ("email", "users", "user", "mail")):
            return True
    if "产线" in question:
        return any(m in nl for m in ("line", "production", "asset", "quality"))
    if "订单" in question and "order" in nl:
        return True
    if nl == "orders" and "订单" in question:
        return True
    if any(k in question for k in ("在哪里", "字段", "列")) and "table=" in context:
        return True
    return False
