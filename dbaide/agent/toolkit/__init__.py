"""Agent tool registry — split into cohesive per-domain handler modules.

`build_tool_registry` wires the same tools as before; each domain module owns a
`register(registry, orchestrator)` that defines its handlers and registers them.
"""
from __future__ import annotations

from dbaide.tools.registry import ToolRegistry
from dbaide.agent.toolkit import (
    schema_tools, catalog_tools, sql_tools, profile_tools, interaction_tools,
    memory_tools, chart_tools, subagent_tools, agenda_tools,
)

# Tools exposed to the Ask loop LLM (catalog CRUD stays on GUI/service only).
LOOP_DECISION_TOOL_NAMES = frozenset({
    "discover_schema", "retrieve_schema_context",
    "list_databases", "list_tables", "describe_table", "inspect_metadata", "retrieve_join_context",
    "list_joins", "validate_joins",
    "generate_sql", "validate_sql", "execute_sql",
    "explain_sql", "optimize_sql", "profile_table", "column_stats",
    "update_agenda",
    "ask_user", "annotate_object",
    "retrieve_turn", "list_earlier_turns",
    "render_chart",
    "run_subagent",
})


def loop_tool_specs(registry: ToolRegistry) -> list:
    return [s for s in registry.list_specs() if s.name in LOOP_DECISION_TOOL_NAMES]


def build_tool_registry(orchestrator) -> ToolRegistry:
    """Register all agent tools bound to an orchestrator instance."""
    registry = ToolRegistry()
    schema_tools.register(registry, orchestrator)
    catalog_tools.register(registry, orchestrator)
    sql_tools.register(registry, orchestrator)
    profile_tools.register(registry, orchestrator)
    interaction_tools.register(registry, orchestrator)
    memory_tools.register(registry, orchestrator)
    agenda_tools.register(registry, orchestrator)
    chart_tools.register(registry, orchestrator)
    if getattr(orchestrator, "subagent_depth", 0) < getattr(orchestrator, "max_subagent_depth", 1):
        subagent_tools.register(registry, orchestrator)
    return registry
