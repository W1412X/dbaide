# DBAide CLI

DBAide is a lightweight command-line data assistant. It connects to local or remote databases, progressively discloses schema only when needed, validates SQL before execution, and answers database questions as an assistant rather than a pure SQL generator.

## Goals

- No vector index or embedding initialization.
- CLI-first workflow.
- Multi-instance and multi-database adapter architecture.
- Safe default query execution: single statement, read-only, timeout, row limit, and `EXPLAIN` preflight where supported.
- Progressive disclosure: instance first, then database/schema, table, column, profile/sample, and execution evidence.

## Quick Start

SQLite works without optional dependencies:

```bash
pip install -r requirements-gui.txt   # CLI + desktop GUI
# or: pip install -r requirements.txt  # CLI only
# or: pip install -e ".[gui]"          # editable install from pyproject.toml

python -m dbaide.cli connect add local --type sqlite --path ./app.db
python -m dbaide.cli ask "这个库有哪些表？" --conn local
python -m dbaide.cli inspect users --conn local
python -m dbaide.cli profile users --conn local
python -m dbaide.cli sql "select * from users" --conn local --execute
```

`connect add` tests the instance and builds offline schema assets by default. New
connections default to the **production** load profile (lowest DB load): builds run
single-threaded with `light` profiling (metadata + key columns), and the agent uses
conservative row/timeout limits. Pass `--load-profile staging|dev` to relax this.

> **Production safety.** DBAide caps concurrent queries, times out every statement,
> never uses `ORDER BY RAND()`, drops large tables to metadata-only profiling, and
> rejects oversized/unfiltered queries. Every SQL it runs is logged — inspect with
> `dbaide queries <conn> --tail 50`. Estimate a build's cost first with
> `dbaide assets build <conn> --dry-run`. See **Resource & Safety** in `docs/DESIGN.md`.

```text
~/.dbaide/assets/instances/<instance>/
  instance.json
  databases.json
  databases/<database>/database.json
  databases/<database>/tables.json
  databases/<database>/tables/<table>/table.json
  databases/<database>/tables/<table>/columns/<column>.json
```

Skip initialization only when you explicitly want to save the connection first:

```bash
dbaide connect add local --type sqlite --path ./app.db --skip-assets
dbaide assets build local
dbaide assets build local --profile-mode all
dbaide assets enrich local --database main --table orders --columns status,total_amount
dbaide assets status local
dbaide assets show local.main.orders.status
```

Column assets are intentionally detailed. A column document stores physical
metadata, inferred semantic role, null/distinct statistics, min/max values,
top-value distribution, sample values, type-specific stats, and usage hints.
Table, database, and instance documents are then synthesized upward from those
column documents.

For programmer lookup workflows:

```bash
dbaide find "用户邮箱在哪" --conn local
dbaide find "订单金额字段" --conn all
```

Developer-focused helpers:

```bash
dbaide tree --conn local
dbaide ddl orders --conn local --database main
dbaide relations --conn local
dbaide doc --conn local --out schema.md
dbaide diff dev.shop prod.shop
```

## Desktop Workbench

DBAide now ships a Tauri + React desktop workbench instead of the old PyQt GUI.
The desktop app uses the same Python core as the CLI for connections, assets,
workflow trace, SQL validation, history, and debug bundles.

```bash
python -m pip install -e .[dev]
npm install
npm run desktop:tauri -- dev
```

The compatibility command starts the desktop app in development when the
workspace checkout is available:

```bash
dbaide-gui
```

Release packaging is configured for Windows, macOS, and Linux:

```bash
npm run desktop:package
```

Desktop capabilities mirror the CLI workflows:

- Connections: create/test SQLite, MySQL/MariaDB, and PostgreSQL instances and build offline assets.
- Assets: hierarchical instance/database/table/column tree, search, and rendered document inspection.
- Ask: Claude/Codex-style answer cards with SQL, result evidence, warnings, assumptions, and trace.
- SQL: validate, explain, and execute read-only SQL through the same guards as the CLI.
- Trace/Plan/Inspector: structured workflow events, generated plan metadata, SQL validation, and execution details.
- History/debug: workflow replay and debug bundle export.

Multiple configured connections are treated as multiple database instances:

```bash
dbaide ask "这些实例里订单相关的表有哪些？" --conn all
dbaide ask "最近 7 天每天订单数" --conn dev,prod --database dev=shop,prod=shop
dbaide ask "每个库里有哪些表？" --conn dev --database all
```

Install as a command:

```bash
pip install -e .
dbaide chat --conn local
```

## Model Configuration

Configuration is stored at `~/.dbaide/config.toml`.

```toml
[models.default]
provider = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
model = "gpt-4.1-mini"
timeout_seconds = 60
```

If no model is configured, DBAide falls back to deterministic local heuristics for schema inspection, profiling, SQL guardrails, simple query generation, and offline asset summaries.

## Architecture

See [docs/DESIGN.md](docs/DESIGN.md) for the full system design (assets → agent loop → execution).

## Packaging

Build installable packages for macOS, Windows, and Ubuntu:

```bash
pip install -e ".[gui,dev]"
./scripts/build_package.sh gui    # desktop bundle → dist/DBAide/
./scripts/build_package.sh wheel  # Python wheel → dist/
```

Details: [docs/PACKAGING.md](docs/PACKAGING.md)

```text
dbaide/
  cli.py                  command-line entry
  config.py               TOML config manager
  llm.py                  configurable OpenAI-compatible client
  models.py               shared dataclasses
  session.py              per-run state
  adapters/               SQLite/MySQL/PostgreSQL adapters
  context/                progressive disclosure state and catalog matching
  tools/                  schema/profile/query/diagnose tools
  agent/                  router, planner, SQL writer, answerer, assistant
  validation/             SQL and schema guards
```
