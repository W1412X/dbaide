# Changelog

All notable changes to DBAide are documented here. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Connection & model import/export** — export a single connection (with joins,
  annotations, and credentials) or all connections + models as a JSON file;
  re-import on any machine. **Settings → Connections → More → Export / Export All**,
  and **Import** in the connection list. Passwords and API keys are exported
  unconditionally (no redaction).
- **MariaDB connection type** — the type selector and CLI now accept `mariadb`
  alongside `mysql`. Both route to the MySQL adapter; MariaDB-specific backslash
  and dialect handling applies.

### Changed

- **Settings dialog layout** — New / Import buttons are now in the list column
  (below the connection or model list), while Save / Test / More remain in the
  form column. This matches the expected mental model: list-level actions near
  the list, form-level actions near the form.
- **Password / API key saved placeholders** — when editing a connection or model
  that already has a credential stored, the password or API key field shows a
  placeholder ("Password saved · leave blank to keep") so users know the
  credential is stored and won't be cleared on save.
- **CSV NULL rendering** — NULL values now appear as literal `NULL` in CSV export
  instead of empty cells, so they are distinguishable from empty strings.

### Fixed

- **`annotations.add()` crash on full import** — the full-import path called the
  non-existent `annotations.upsert()` method, causing an `AttributeError` whenever
  a full import included annotations. Fixed to use `annotations.add()`.
- **Backslash escaping in SQL INSERT export** — trailing backslashes in string
  values only had backslash doubling for MySQL/MariaDB, leaving other dialects
  with broken SQL (the `\` escaped the closing quote). Backslashes are now doubled
  for all dialects.
- **XSS in Markdown HTML sanitizer** — unquoted HTML event handlers
  (`onerror=alert(1)`) bypassed the sanitization regex. The regex now handles
  both quoted and unquoted attribute values.
- **Dialect-aware INSERT export in data browser** — "Copy as INSERT" in the
  data browser now receives the current connection's SQL dialect (MySQL, PostgreSQL,
  SQLite) through the full view chain, so backslash and identifier quoting match
  the target database.
- **Config file corruption on save→reload→save cycle** — `_render_toml` placed
  `default_connection` and `default_model` after the `[meta]` table header, so
  TOML scoping absorbed them into `meta` on reload. A subsequent save wrote them
  twice under `[meta]`, producing `Cannot overwrite a value` on the next load.
  Root-level keys now render before any `[table]` header. The reload path also
  recovers keys that were previously absorbed into `meta`.
- **Config wipe on parse failure** — when `config.toml` had a TOML syntax error,
  `reload()` fell back to an empty config, migrated it, and saved — overwriting
  the user's (possibly recoverable) file with empty data. The save is now skipped
  when parsing fails.
- **Connection names with dots produce invalid TOML** — a connection named
  `my.server` generated `[connections.my.server]` (nested tables) instead of
  `[connections."my.server"]`. Names are now quoted when they contain dots, spaces,
  or other TOML-special characters.
- **Streaming text loss on RuntimeError recovery** — when the answer widget was
  destroyed mid-stream (PyQt RuntimeError), recreating it reset the accumulated
  text, losing all chunks received so far. The unnecessary reset is removed;
  `begin_turn()` handles the normal-path reset.
- **Schema guard dead code** — an unreachable duplicate CTE check and a redundant
  condition were removed from `validate_table_refs`, simplifying the logic.
- **LLM non-streaming JSON decode crash** — `json.JSONDecodeError` (a `ValueError`
  subclass) escaped the retry loop, crashing the run on malformed model responses.
  `ValueError` is now caught alongside `URLError`/`TimeoutError`/`OSError`.
- **Float conversion crash on non-numeric confidence** — `float("high")` in
  `sql_writer` and `schema_context` raised `ValueError`. Both sites now guard
  with try-except.
- **Stale widget references after session switch** — `_live_answer`,
  `_live_answer_text`, and `_clarification_bar` were not reset in `clear()`,
  leaving dangling references to deleted widgets.
- **Composer attach button enabled during execution** — the "+" context button
  remained clickable while a query was running. It is now disabled alongside the
  input and model selector.
- **Port field crash on malformed config** — `int(port)` in `ConnectionForm.load()`
  could crash the settings dialog on non-numeric port values. Guarded with
  try-except.
- **Join editor missing required-field validation** — `_edit()` in the joins tab
  lacked the same required-field check that `_add()` had. Both now validate that
  all four endpoint fields are populated.
- **Non-atomic file writes in session / workflow / query-history stores** — all
  three stores used `path.write_text()` which is not atomic: a process crash
  mid-write could truncate the file, silently destroying the session, workflow
  result, or query history. Writes now use `tempfile.mkstemp()` + `os.replace()`
  (the same pattern already used by `ConfigManager` and `AssetStore`).
- **Unicode / CJK connection names produce invalid TOML** — `_toml_key()` used
  Python's `str.isalnum()` to decide whether to quote, but `isalnum()` returns
  `True` for CJK characters while TOML bare keys only allow `[A-Za-z0-9_-]`.
  A connection named `数据库` generated a bare key that `tomllib` rejected on
  reload, making the entire config unreadable. The check now requires
  `ch.isascii()` so non-ASCII names are always quoted.
- **Path traversal in desktop debug bundle filename** — `connection_name` from
  the running session was used unsanitised in the debug ZIP filename. A name
  containing `/` or `..` could place the bundle outside `~/.dbaide/debug/`.
  The name is now collapsed to filesystem-safe characters before building the
  filename.
- **Double-close of physical connection when pool validator raises** — when a
  connection validator threw an exception, `_valid()` closed the connection
  internally and then the caller (`acquire`/`release`) closed it again. The
  second close was swallowed by `try/except` but violated the exactly-once
  contract. `_valid()` now only returns False on exception; callers handle
  closing.
- **SQL string parser ignores dialect for backslash escaping** — both
  `_strip_strings_and_comments` (security validation) and `_sql_top_level`
  (LIMIT detection) treated backslash as a string escape in all dialects.
  Standard SQL (PostgreSQL, SQLite) does NOT use backslash escaping — only
  MySQL does. A string literal ending with `\` caused the parser to lose track
  of quote boundaries, potentially hiding forbidden keywords from the SQL guard
  or missing an existing LIMIT clause. Both parsers, plus `outer_limit_value`
  and `append_limit`, now accept a `dialect` parameter and only apply backslash
  escaping for MySQL/MariaDB.
- **Unguarded `float()` on confidence values crashes join pipeline** — several
  sites in `sql_writer.py`, `join_validation.py`, `joins/catalog.py`,
  `joins_tab.py`, and `service.py` called `float(confidence)` on LLM-produced
  or stored values without try/except. A non-numeric string like `"high"`
  raised `ValueError`, crashing the entire SQL generation or join validation
  step. All sites now use a `_safe_confidence` / `_safe_float` helper that
  returns `0.0` on failure.

## [0.0.6] — 2026-06-08

### Added

- **Batched tool calls** — one decision may now carry several independent read-only
  evidence calls (e.g. describe two tables + profile a column) and the loop runs
  them in order, decides once from all results. Significantly cuts the number of
  LLM round-trips on data questions. The generate→validate→execute SQL chain,
  ask_user, and writes stay one-per-decision so each keeps its safety gate.

### Changed

- **New app icon** — three concentric arcs in a polished-graphite gradient on a
  transparent canvas. Single mark, restrained, reads well on both light and dark
  surfaces. Same SVG drives the macOS `.icns`, the Windows `.ico`, and the in-app
  window/dock icon.
- **UI polish** — header now shows the app mark next to the wordmark; workbench
  tabs no longer clip "Query 1" to "Quer…"; the Connection dialog uses the app's
  accent/ghost buttons instead of a native button box so dialogs read consistently.

### Fixed

- **Trace tree mangled after a clarification** — when an `ask_user` pause was
  followed by the user replying, the resumed steps used the same `decision:N` /
  `step:N` node ids as the pre-pause portion of the same turn. The desktop's
  TraceModel keyed nodes by id, so the resumed steps silently overwrote the
  earlier ones — work disappeared from the tree. Step numbering is now carried
  across the pause so every node id stays unique within a turn.
- **The "Waiting for user clarification" marker sat outside the loop** — it was
  emitted at the trace root with no parent. Now it nests under the `ask_user`
  tool step, so the hierarchy reads *loop → ask_user → Waiting…*.

## [0.0.5] — 2026-06-07

### Added

- **App icon** — a minimalist database mark (a blue "data" top on a dark squircle,
  in the app's accent colour) now ships for the macOS app, the Windows installer,
  and the running window / dock / taskbar.
- **Verified-knowledge tier in working memory** — the agent separates conclusions
  it has *verified* with tool evidence (or that you confirmed) from tentative
  observations and guesses, so each decision can rely on what is actually settled
  instead of re-litigating it.
- **Paginated, range-aware tools** — `profile_table` windows columns with
  `column_offset`/`column_limit` and reports `total_columns`; `column_stats`
  exposes `top_k`; `inspect_metadata` reports `total_tables`. No column, table, or
  value is silently skipped — the tool says how many exist and how to fetch the rest.

### Changed

- **Working memory reads did-what → result → judgment** — every step records why it
  ran, a readable one-line result (instead of a raw JSON dump), and the model's
  assessment of the outcome, so the agent keeps a clear, honest account of progress.
- **No silent truncation anywhere the agent looks** — every capped list it sees
  (table columns, candidate tables, distinct values, join relations, SQL result
  rows) now signals "+N more" *and* how to get the rest (retrieve the archived
  result, page with a range parameter, or use SQL `LIMIT`/`OFFSET`).
- **Leaner decision prompt** — de-duplicated the agent's instructions (~3.1k → ~2.5k
  tokens) with no change in behaviour.

### Fixed

- **The agent could miss the column or table a question depended on.** Schema
  evidence silently capped at the first 10 columns / 8 candidate tables, so it could
  query the wrong field (e.g. searching `username` while the name lived in
  `nick_name`) and then spiral. It now sees them, or is explicitly told they exist
  and how to load them.
- **A clarification request could crash the run.** When the model phrased `ask_user`
  as the action rather than a tool call, the loop failed the whole run; it now
  coerces the shape and pauses to ask you, as intended.
- **Row-capped SQL results are flagged** so the agent does not report a truncated
  list as if it were complete.

## [0.0.4] — 2026-06-07

### Added

- **Streaming answers** — the assistant's final answer now streams in token-by-token
  over SSE as the model writes it, so the first words appear immediately. Only the
  final answer streams; intermediate tool steps don't. Toggle in **Settings → General**
  ("Reveal answers progressively", default on); when off, or when the model can't
  stream, the answer renders once it's ready. No front-end simulation — what you see
  is the real generation.

### Changed

- **Proactive business-caliber clarification** — the agent now applies one clear
  principle: separate what the **data can reveal** (table/column existence, what
  values a column holds, how tables relate — discovered with tools, never asked)
  from what only **your intent can decide**, and it confirms the latter before
  answering whenever the question, the schema, the data, your saved notes, and
  today's date still can't pin down which interpretation you mean — e.g. an
  under-specified time range, what a metric actually counts, how a qualitative
  judgement becomes a concrete rule, or which records are included. It asks one
  consolidated question with concrete options instead of silently picking a
  default and reporting a subtly wrong number; your confirmed answers are applied
  verbatim to the generated SQL. Today's date is now given to the agent so
  genuinely relative periods resolve on their own.

### Fixed

- **Agent no longer aborts on valid multi-line answers** — a finish answer whose
  markdown contained a real newline made strict JSON parsing fail (`Invalid control
  character`) and killed the run at the last step. Parsing now tolerates control
  characters in strings, and a malformed decision is retried instead of crashing.
- **Cancellation during answer streaming** — cancelling mid-stream now stops
  immediately instead of being swallowed into a wasteful non-stream re-request.

### Internal

- Removed a large amount of dead code with no behaviour change: the orphaned
  `eval/` package, unreferenced functions across the agent/adapters/core/history/
  rendering layers, and test-only helpers; tidied stale imports. Refreshed
  `docs/DESIGN.md` to match the code.

## [0.0.3] — 2026-06-05

### Fixed

- **macOS desktop app launch** — CI and local builds now ad-hoc sign the `.app`
  bundle (`scripts/codesign_macos.sh`) so PyInstaller packages open on macOS 15+
  without silently exiting. Release workflow includes a macOS startup smoke test
  before publishing the DMG.

## [0.0.2] — 2026-06-05

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

[Unreleased]: https://github.com/W1412X/dbaide/compare/v0.0.6...HEAD
[0.0.6]: https://github.com/W1412X/dbaide/compare/v0.0.5...v0.0.6
[0.0.5]: https://github.com/W1412X/dbaide/compare/v0.0.4...v0.0.5
[0.0.4]: https://github.com/W1412X/dbaide/compare/v0.0.3...v0.0.4
[0.0.3]: https://github.com/W1412X/dbaide/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/W1412X/dbaide/compare/v0.1.0...v0.0.2
[0.1.0]: https://github.com/W1412X/dbaide/releases/tag/v0.1.0
