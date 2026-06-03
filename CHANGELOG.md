# Changelog

All notable changes to DBAide are documented here. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/W1412X/dbaide/releases/tag/v0.1.0
