"""Managed prompts for DBAide agent."""
from dbaide.agent.prompts.router import ROUTER_SYSTEM_PROMPT
from dbaide.agent.prompts.sql_writer import SQL_WRITER_SYSTEM_PROMPT, SQL_WRITER_USER_TEMPLATE
from dbaide.agent.prompts.result_interpreter import RESULT_INTERPRETER_SYSTEM_PROMPT

__all__ = [
    "ROUTER_SYSTEM_PROMPT",
    "SQL_WRITER_SYSTEM_PROMPT",
    "SQL_WRITER_USER_TEMPLATE",
    "RESULT_INTERPRETER_SYSTEM_PROMPT",
]
