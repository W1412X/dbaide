"""SKILL document for AI agent integration.

Generates a Markdown document that teaches any AI agent (Claude Code, Cursor,
Codex, Trae, etc.) how to use dbaide via its CLI.  The document is emitted by
``dbaide skill`` and injected by ``dbaide setup <tool>`` into the correct
config location for the target agent.
"""

from __future__ import annotations

from dbaide import __version__


def skill_document(*, connection_hint: str = "") -> str:
    """Return the full SKILL Markdown document."""
    conn_flag = f" --conn {connection_hint}" if connection_hint else ""
    return f"""\
# DBAide — Database Assistant (v{__version__})

DBAide is a CLI database assistant. It explores, queries, and explains
relational databases (MySQL / PostgreSQL / MariaDB / SQLite) from offline
schema assets and live connections. **Use it whenever you need database
context in your coding work.**

---

## Quick Reference

```bash
# Natural-language question → SQL → result (the single most useful command)
dbaide ask "Show me the top 10 customers by revenue"{conn_flag}

# Search schema for tables/columns matching a keyword
dbaide find "user email"{conn_flag}

# Print the full schema tree
dbaide tree{conn_flag}

# Execute raw SQL
dbaide sql "SELECT count(*) FROM orders" --execute{conn_flag}
```

---

## Command Catalog

### 1. Natural-Language Query (core)

| Command | Description |
|---------|-------------|
| `dbaide ask "<question>"` | Generates SQL from a natural-language question, executes it, and returns a formatted answer. |
| `dbaide ask "<question>" --json` | Same, but output as structured JSON (handy for piping). |
| `dbaide ask "<question>" --debug-trace` | Include the full agent reasoning trace. |
| `dbaide chat` | Interactive multi-turn conversation mode. |

**Common flags:** `--conn <name>`, `--database <db>`, `--limit <n>`, `--timeout <s>`.

### 2. Schema Exploration

| Command | Description |
|---------|-------------|
| `dbaide find "<query>"` | Fuzzy-search tables and columns in offline assets. |
| `dbaide tree` | Print the schema tree (databases → tables → columns). |
| `dbaide ddl <table>` | Show `CREATE TABLE` DDL. |
| `dbaide inspect <table>` | Detailed table structure (columns, types, keys). |
| `dbaide relations` | Foreign keys and discovered join hints. |
| `dbaide doc` | Export the full schema as Markdown. |
| `dbaide diff <left> <right>` | Diff two instance schemas (e.g. `dev` vs `prod`). |

### 3. Data Analysis

| Command | Description |
|---------|-------------|
| `dbaide sql "<sql>" [--execute]` | Validate (and optionally execute) a SQL statement. |
| `dbaide diagnose "<sql>"` | Run validation + `EXPLAIN` analysis on a query. |
| `dbaide profile <table>` | Column-level statistics (nulls, distinct, top values). |
| `dbaide queries` | Recent query audit log for the connection. |

### 4. Asset Management

| Command | Description |
|---------|-------------|
| `dbaide assets build <conn>` | Build offline schema cache for a connection. |
| `dbaide assets status` | Show build status for all connections. |
| `dbaide assets show <path>` | Show a raw asset document (e.g. `prod.shop.orders`). |
| `dbaide assets enrich <conn>` | Re-profile a specific table. |

### 5. Connection Management

| Command | Description |
|---------|-------------|
| `dbaide connect add <name> --type <t>` | Add or update a connection. |
| `dbaide connect list` | List all configured connections. |
| `dbaide connect test [name]` | Test connectivity. |

### 6. Model / LLM Configuration

| Command | Description |
|---------|-------------|
| `dbaide model list` | List configured LLM models. |
| `dbaide model add <name> --provider openai_compatible --base-url <url> --model <model>` | Add/update an LLM model. |
| `dbaide model delete <name>` | Remove a model config. |
| `dbaide model set-default <name>` | Set the default model. |
| `dbaide model test [name]` | Test an LLM model with a probe query. |

### 7. Configuration

| Command | Description |
|---------|-------------|
| `dbaide config show` | Show resource defaults and agent parameters. |
| `dbaide config set <key> <value>` | Set a resource default (e.g. `max_workers 4`). |
| `dbaide config reset` | Reset resource defaults to built-in values. |

### 8. Schema Notes

| Command | Description |
|---------|-------------|
| `dbaide annotate add "<note>" --table <t>` | Annotate a table/column/database. |
| `dbaide annotate list` | List all annotations. |
| `dbaide annotate rm --id <id>` | Remove an annotation. |

### 9. Join Catalog

| Command | Description |
|---------|-------------|
| `dbaide join list` | List known join relationships. |
| `dbaide join add --table <t> --column <c> --ref-table <rt> --ref-column <rc>` | Add a join hint. |
| `dbaide join delete --id <id>` | Remove a join hint. |

### 10. Sessions & History

| Command | Description |
|---------|-------------|
| `dbaide session list` | List saved chat sessions. |
| `dbaide session show <id>` | Display a session's conversation. |
| `dbaide session delete <id>` | Delete a session. |
| `dbaide history list` | List recent workflow runs. |
| `dbaide history delete <id>` | Delete a history entry. |

### 11. Export / Import

| Command | Description |
|---------|-------------|
| `dbaide export --conn <name>` | Export a connection (config + joins + notes) as JSON. |
| `dbaide export --all` | Export everything (all connections, models, config). |
| `dbaide import <file>` | Import from a previously exported JSON file. |

### 12. Integration

| Command | Description |
|---------|-------------|
| `dbaide skill` | Print this SKILL document (pipe into agent configs). |
| `dbaide setup <tool>` | Auto-configure integration (claude, cursor, codex, …). |

---

## Recommended Workflows

### "I need to understand this database"
```bash
dbaide tree --conn prod                     # big picture
dbaide find "order"                         # find relevant tables
dbaide inspect orders --conn prod           # column-level detail
dbaide relations --conn prod                # how tables connect
dbaide profile orders --conn prod           # data distribution
```

### "Answer a data question in my code"
```bash
dbaide ask "What's the average order value by country last month?" --conn prod
# → returns formatted answer with the generated SQL
```

### "I have SQL but want to verify it"
```bash
dbaide sql "SELECT u.name, count(o.id) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.name" --conn prod
# → validates without executing (shows normalized SQL or issues)

dbaide diagnose "SELECT ..." --conn prod
# → validation + EXPLAIN plan analysis
```

### "Compare dev vs prod schema"
```bash
dbaide diff dev prod
# → shows missing tables, column differences, type changes
```

### "Multi-database query"
```bash
dbaide ask "Compare user counts across instances" --conn dev,staging,prod
dbaide ask "List all databases" --conn all
```

---

## Tips for AI Agents

1. **`dbaide ask` is your primary tool** — it handles SQL generation,
   execution, safety checks, and formatting in one call.
2. **Use `--json` when you need structured output** for further processing.
3. **Search before you ask** — `dbaide find` and `dbaide tree` help you
   understand the schema so you can ask precise questions.
4. **`dbaide sql --execute`** when you already have exact SQL and don't
   need the agent to generate it.
5. **Chain commands**: `find` → `inspect` → `ask` for targeted deep-dives.
6. **All commands support `--conn`** to specify which database connection to use.
7. **`dbaide connect list`** shows available connections and their status.
"""


# ── Integration templates ───────────────────────────────────────────────────

_WRAPPER_HEADER = """\
# DBAide Integration
#
# Auto-generated by `dbaide setup {tool}`.  Safe to edit; re-running the
# command will overwrite this file.

"""


def claude_code_content(*, connection_hint: str = "") -> str:
    """Content for CLAUDE.md / .claude/commands/dbaide.md."""
    body = skill_document(connection_hint=connection_hint)
    return _WRAPPER_HEADER.format(tool="claude") + body


def cursor_rules_content(*, connection_hint: str = "") -> str:
    """Content for .cursor/rules/dbaide.mdc."""
    body = skill_document(connection_hint=connection_hint)
    return f"""\
---
description: DBAide database assistant — use for any database query or schema exploration
globs:
alwaysApply: true
---

{body}
"""


def codex_content(*, connection_hint: str = "") -> str:
    """Content for codex.md / AGENTS.md."""
    body = skill_document(connection_hint=connection_hint)
    return _WRAPPER_HEADER.format(tool="codex") + body


def generic_rules_content(*, connection_hint: str = "", tool: str = "agent") -> str:
    """Generic rules file content (Trae, Windsurf, QCoder, etc.)."""
    body = skill_document(connection_hint=connection_hint)
    return _WRAPPER_HEADER.format(tool=tool) + body


# Map of tool name → (config file path relative to project root, content generator)
TOOL_CONFIGS: dict[str, tuple[str, str]] = {
    "claude":    (".claude/commands/dbaide.md", "claude"),
    "cursor":    (".cursor/rules/dbaide.mdc", "cursor"),
    "codex":     ("codex.md", "codex"),
    "trae":      (".trae/rules/dbaide.md", "generic"),
    "windsurf":  (".windsurfrules/dbaide.md", "generic"),
    "augment":   (".augment/rules/dbaide.md", "generic"),
    "opencode":  (".opencode/rules/dbaide.md", "generic"),
    "qcoder":    (".qcoder/rules/dbaide.md", "generic"),
    "mimocode":  (".mimocode/rules/dbaide.md", "generic"),
    "roo":       (".roo/rules/dbaide.md", "generic"),
    "cline":     (".cline/rules/dbaide.md", "generic"),
    "aider":     (".aider/rules/dbaide.md", "generic"),
}

SUPPORTED_TOOLS = sorted(TOOL_CONFIGS.keys())


def generate_config(tool: str, *, connection_hint: str = "") -> tuple[str, str]:
    """Return (relative_path, content) for the given tool.

    Raises KeyError if *tool* is not in TOOL_CONFIGS.
    """
    rel_path, flavour = TOOL_CONFIGS[tool]
    kw = {"connection_hint": connection_hint}
    if flavour == "claude":
        content = claude_code_content(**kw)
    elif flavour == "cursor":
        content = cursor_rules_content(**kw)
    elif flavour == "codex":
        content = codex_content(**kw)
    else:
        content = generic_rules_content(tool=tool, **kw)
    return rel_path, content
