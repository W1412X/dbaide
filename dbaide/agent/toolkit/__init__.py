"""Agent tool registry — split into cohesive per-domain handler modules.

`build_tool_registry` wires the same tools as before; each domain module owns a
`register(registry, orchestrator)` that defines its handlers and registers them.
"""
from __future__ import annotations

from dbaide.tools.registry import ToolRegistry
from dbaide.agent.toolkit import (
    schema_tools, catalog_tools, sql_tools, profile_tools, interaction_tools,
)
# Re-exported for tests/tools that import these helpers from `dbaide.agent.toolkit`.
from dbaide.agent.toolkit.support import (  # noqa: F401
    _expand_to_full_columns, _sample_observed_values, _remember_table_schema,
)

# Tools exposed to the Ask loop LLM (catalog CRUD stays on GUI/service only).
LOOP_DECISION_TOOL_NAMES = frozenset({
    "discover_schema", "resolve_schema", "synthesize_schema_answer",
    "list_databases", "list_tables", "describe_table", "get_relations", "list_joins", "validate_joins",
    "clarify_semantics", "generate_sql", "validate_sql", "execute_sql",
    "execute_readonly_sql", "explain_sql", "profile_table", "column_stats",
    "ask_user", "annotate_object",
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
    return registry
