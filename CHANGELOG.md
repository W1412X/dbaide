# Changelog

All notable changes to DBAide are documented here. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added — Workbench (a read-only database client)

The desktop app gains an **Assistant / Workbench** mode switch. The Workbench is a
DBeaver-style, multi-document workspace, all routed through the same read-only
guardrails as the agent:

- **Multi-document tabs** — multiple SQL editors and per-table viewers open at once,
  closeable and re-orderable, with a pinned Query History tab. Shortcuts: `⌘1`/`⌘2`
  (mode), `⌘T` (new editor), `⌘W` (close).
- **SQL editor** — schema-aware autocomplete, line numbers, current-line highlight,
  **Format**, **Explain** (query plan), comment toggle (`⌘/`), and run the **selection
  or the statement under the cursor** (`⌘↵`).
- **Data browser** — paginated/sortable/filterable grid, row-number gutter, on-demand
  exact **row count**, inline value viewer with JSON pretty-printing, and **foreign-key
  navigation** (right-click a FK cell → open the referenced row).
- **Structure** — columns, foreign-key relations (in/out, clickable), indexes, and a
  generated, copyable `CREATE TABLE`.
- **Query history** — per-connection, click to load, double-click to run.
- **Export** — copy or save results as CSV / JSON / Markdown / `INSERT`.
- **Schema tree** — right-click to open a table or **Generate SQL** templates; copy
  (qualified) names.

### Changed

- Opening a table shows its (offline, instant) **Structure** first; the data query
  runs lazily only when you open the Data tab.
- The Trace / Inspector activity panel is now Assistant-only; the Workbench uses the
  full width.
- Connection forms show only the fields relevant to the selected type.

## [0.1.0] — 2025

First public release: a local-first AI database assistant available as both a CLI
and a PyQt6 desktop app, sharing one Python core.

### Highlights

- **Agentic Ask** — a tool loop discovers schema, maps joins, writes and validates
  read-only SQL, executes it, and interprets the result, streaming every step to a
  Trace panel.
- **Never-guess clarification** — when a question is ambiguous (which table, what a
  status value means, which timezone, what a metric counts), the agent asks you to
  confirm instead of inventing a default.
- **Safe by default** — read-only single statements, per-statement timeout, row caps,
  `EXPLAIN` cost gate, confirmation on risky queries, and a log of every executed SQL.
- **Concurrent sessions** — run multiple conversations at once, capped by a
  configurable limit; switch between them while they work.
- **SQL workspace** — editor with `⌘↵` to run, flat result grid, and "Open in SQL"
  from any agent answer.
- **Progressive schema assets** — offline instance → database → table → column
  documents accelerate discovery; live adapters are the fallback.
- **Databases**: SQLite, MySQL/MariaDB, PostgreSQL. **Languages**: English, 简体中文
  (answers follow the UI language).
- **Rich Markdown** answers (mistune): tables, code, blockquotes, lists.

### Packaging

- Native installers built and published by CI for **macOS (`.dmg`,
  drag-to-Applications)**, **Windows (`.msi` wizard)**, and **Linux (`.tar.gz`)** —
  pushing a `v*` tag cuts a GitHub Release automatically.

[Unreleased]: https://github.com/W1412X/dbaide/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/W1412X/dbaide/releases/tag/v0.1.0
