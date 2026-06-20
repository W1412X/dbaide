# DBAide Screenshot Tour

This page is the visual companion to [README.md](../README.md) and
[DESIGN.md](DESIGN.md). Images below come from the current codebase: application
screenshots are rendered via [`tools/shoot_docs.py`](../tools/shoot_docs.py) and
[`tools/shoot_promo.py`](../tools/shoot_promo.py), which now require the real
Qt WebEngine path for answer charts and fail loudly instead of falling back.

## 1. Business analysis workflow

### Offline assets and progressive discovery

DBAide does not dump an entire schema into the prompt. It builds or reads offline
assets, then narrows connection → database → table → column.

![Assets initializing](images/promo/01-assets-initializing.png)

### Natural-language analysis workflow

The assistant can take a broad business question, choose tables, validate joins,
plan the analysis path, and keep the run visible while it prepares evidence.

![Analysis workflow](images/promo/02-runtime-thinking.png)

### Native chart rendering inside the answer flow

The chart agent produces structured chart specs, and the desktop renderer turns
them into ECharts inside the same Qt WebEngine answer document. The screenshots
below are captured from the current chat surface, not from standalone sample cards.

![Chart answer](images/promo/03-chart-answer-analysis.png)

![Chart answer breakdown](images/promo/04-chart-answer-breakdown.png)

### Clarification instead of guessing

When the requested metric has business ambiguity, DBAide pauses and asks the user
to confirm the accounting rule, time attribution, or threshold.

![Clarification](images/promo/05-clarification.png)

### Runtime visibility and trace

The assistant is not a one-shot black box. The UI exposes the live turn, the
generated answer, and the agent trace entry point from the same conversation flow.

![Runtime thinking](images/promo/02-runtime-thinking.png)

### Trace timeline drawer

The full trace opens in a dedicated right-side drawer, preserving the timeline,
durations, and per-step detail without pushing raw event JSON into the main chat.

![Agent trace](images/promo/17-agent-trace.png)

## 2. Developer investigation workflow

### Explore the right field instead of fabricating one

If a developer asks for a field that does not exist, DBAide can search schema,
read table structure, validate join paths, and rewrite the query around the real
columns.

![Field exploration](images/promo/08-developer-field-exploration.png)

### Cross-table consistency audit

DBAide can also work as a debugging assistant: aggregate at a stable grain, compare
orders, payments, refunds, and ledgers, then classify anomaly buckets.

![Consistency audit](images/promo/09-developer-consistency-audit.png)

### Full SQL workbench

The desktop app includes a multi-document workbench for SQL editing, structure
inspection, browsing, exporting, and validating results.

![Workbench SQL](images/promo/06-database-client-sql.png)

![Workbench table](images/promo/07-database-client-table.png)

## 3. Configuration, safety, and operations

### Connection management

Connections are managed in one place and share the same config with the CLI.
Import/export lives on this screen and supports portable environment transfer.

![Settings connections](images/promo/10-settings-connections.png)

### Model configuration

Provider, base URL, model id, timeout, API key, and context length are explicitly
configured rather than hidden in prompt glue.

![Settings models](images/promo/11-settings-models.png)

### Resource and safety limits

This page exposes the hard limits that keep the app safe on real databases:

- max concurrent runs
- max concurrent queries
- statement timeout
- default and maximum row limits
- large LIMIT confirmation threshold
- big-table threshold
- EXPLAIN cost gate
- join sample size
- agent step budget
- prior-turn window
- latest result truncation budget
- compression threshold

![Settings resources](images/promo/12-settings-resources.png)

### MCP / coding-tool integrations

DBAide can register itself as an MCP server for Claude, Codex, Cursor, Roo,
Gemini, Qwen, Windsurf, and Opencode, with three integration modes:

- `full`: ask + atomic tools
- `ask`: high-level ask only
- `tools`: atomic database tools only

![Settings integrations](images/promo/13-settings-integrations.png)

### Backup manager

Backups are first-class: generate a table/database export and browse local backup
history with size, row count, format, and date.

![Backup manager](images/promo/14-backup-manager.png)

### Partial asset build dialog

Asset building is scoped and controllable. You can pick databases, set concurrency,
and enforce a time budget.

![Build assets dialog](images/promo/15-build-assets-dialog.png)

### Safe connection setup

Connection setup includes load profile, session timezone, and SSL mode, so the app
can stay conservative on production systems by default.

![Connection dialog](images/promo/16-connection-dialog.png)

## 4. What these screenshots imply about the design

These UI states reflect a few non-negotiable design choices:

- **Local-first**: databases are reached directly from the desktop app or CLI.
- **Read-only by default**: the assistant writes only validated `SELECT` queries.
- **Progressive disclosure**: schema evidence is earned instead of dumped.
- **Structured chart planning**: the model chooses chart intent, not raw ECharts code.
- **Visible execution**: traces, SQL, limits, and exports stay inspectable.
- **One core, two surfaces**: CLI and desktop share config, safety policy, and tools.

For a narrative product overview in Chinese, see
[BLOG.zh-CN.md](BLOG.zh-CN.md).
