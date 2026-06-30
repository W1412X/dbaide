# Changelog

All notable changes to DBAide are documented here. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- **The SQL optimizer is now an agent tool, not an auto-gate.** The agent has an
  `optimize_sql` tool it calls when it wants advice on a query it expects to be expensive
  (one model call over SQL + EXPLAIN plan + relevant schema → suggestions); it then writes a
  better query itself. `execute_sql` no longer auto-runs the optimizer or pauses execution —
  for a query over `optimize_advise_rows` it just attaches a lightweight hint pointing at the
  tool. This removes the framework-side "did this get optimized?" tracking entirely (no gate,
  no one-shot flag, no loop risk) and matches dbaide's agentic model: the agent owns
  optimization. (The previous `optimize_advise_mode` gate/suggest knob is gone.) The Workbench
  ⚡ button is unchanged. Suggestions follow the global answer language.

## [0.9.21] — 2026-06-29

### Added

- **LLM SQL optimization advisor.** A dedicated single-call optimizer agent looks at a
  query together with its EXPLAIN plan and the relevant table schema (columns, indexes,
  foreign keys) and returns concrete optimization *suggestions*. Two entry points:
  - **Agent flow** — after the model generates a query, if its EXPLAIN cost exceeds the
    new `optimize_advise_rows` threshold (default 1,000,000 rows; 0 = off), the suggestions
    are attached to the tool result + agent trace. It's advisory only: it never rewrites
    the SQL and never blocks — the main agent decides whether to issue a better query, and
    only the existing `explain_max_rows` gate can still require confirmation.
  - **Workbench** — a ⚡ button in the SQL editor runs the optimizer on the current SQL
    (auto-fetching its EXPLAIN plan + schema) and shows the suggestions **inline in an
    Advice tab** next to Result/Messages, so the SQL stays visible (no modal).

  Reuses the default model; set `[resource_defaults].optimizer_model` to a configured
  model name to use a different one. Threshold lives in **Settings → Resources**. (SQLite
  gives no row estimate, so the agent-flow trigger stays quiet there; the Workbench button
  works on any connection that has a model configured.)

## [0.9.20] — 2026-06-29

### Changed

- **Roomier SQL pool dialog** — bigger by default (1040×560) with the Running and Queued
  lists side by side instead of stacked vertically; when the governor is off the queue
  column collapses so Running spans the full width. SQL cells show the full statement on
  hover.

## [0.9.19] — 2026-06-29

### Changed

- **The SQL cost governor is now on by default** with a generous `max_inflight_cost`
  budget of 50,000,000 (EXPLAIN-estimated rows) — chosen ≥ the per-query cost gate of the
  production (5M) and staging (20M) profiles, so it never rejects a single query those
  profiles already allow; it caps the *concurrent* total and powers the live pool view
  out of the box. The trade-off: each query now runs a quick EXPLAIN to estimate its
  cost. Set the budget to 0 in **Settings → Resources** to turn the governor off (the
  pool then falls back to a no-overhead monitor). SQLite gives no estimate, so it stays a
  monitor there regardless.
- **The SQL pool is now a live monitor even when the cost governor is off.** Previously
  the status-bar indicator only appeared after you set a budget, so there was no way to
  just *watch* running SQL. Now it tracks and shows in-flight queries with no gating
  (cost shows as "—" since it isn't estimated when off); setting `max_inflight_cost` > 0
  adds the budget bar + FIFO queue on top. The indicator sits at the right of the bottom
  status bar — visible whenever the governor is armed or anything is running.

## [0.9.18] — 2026-06-29

### Added

- **SQL cost governor** — a process-wide admission control over the query-execution
  path, keyed to a new `max_inflight_cost` budget (EXPLAIN-estimated rows; `0` = off,
  the default). No single query may cost more than the budget, and the costs of the
  governed queries executing at once can't sum past it — over-budget queries wait in a
  **FIFO queue** and are admitted as budget frees up; a query that exceeds the whole
  budget is rejected up front. It covers the `execute_sql` path (agent answers, the SQL
  editor, dashboards, table browsing, the MCP `execute_sql` tool), global across
  connections; the budget is enforced *after* read-only validation, table-scope, and the
  confirmation gate, never before. Bulk schema introspection / asset building and the
  dedicated profiling/sampling tools (`sample_rows`, `column_stats`, `profile_table`)
  run under their own concurrency cap (`max_inflight_queries`), not this budget. A live
  **SQL pool** status-bar indicator (running/queued counts + budget use) opens a dialog
  listing each running and queued query with its cost, connection, and timing. Configure
  the budget in **Settings → Resources**. (SQLite gives no row estimate, so the governor
  is effectively inert there; it bites on MySQL/PostgreSQL.) Known limitation: a query
  cancelled while still *queued* is not yet interrupted — it runs (read-only) when it
  reaches the head of the queue.

## [0.9.17] — 2026-06-29

A dashboards UX pass plus an app-wide button-readability fix.

### Added

- **Tabbed dashboards with a view/edit split** — opening a saved board now shows it
  **view-only** (filters + charts, no model picker or refine box — those belong to
  editing); an **Edit** button reveals the generate/refine controls. Generating a new
  board opens an edit tab. Multiple boards open side-by-side as closable tabs, with a
  corner button back to the gallery. Model bootstrap (which reads connection docs from
  disk) is now lazy — a view-only tab pays nothing — and a board that finishes building
  after its tab is closed no longer renders into a torn-down view.

### Fixed

- **Primary buttons were unreadable in light mode** — under the Fusion style a
  `QPushButton` ignores a `background` set via a global *property* selector
  (`[primary="true"]`), so every primary button (send, Save, Confirm, Build, Install,
  dashboard Open/Apply, …) rendered as an unfilled outline with white text — invisible
  on a light background. They now paint the accent fill reliably via a direct stylesheet.
- **Dashboard tab bar matches the rest of the app** — it reused a local stylesheet that
  dropped the app-wide closable-tab chrome (the Workbench `panelTabs` style); now it
  uses the shared style, with a themed close button, a vertically-centered corner icon,
  and full-name tooltips on elided tabs.
- **Trimmed the dashboard's oversized window margins** — the web view now runs
  edge-to-edge (content inset ~34px → ~16px), with the title kept aligned to the content.

## [0.9.16] — 2026-06-28

A hardening release from a codebase-wide audit + fuzzing pass. Every fix is verified
and covered by a regression test.

### Fixed

- **Charts never crash on degenerate data** — the gauge, heatmap, sankey and boxplot
  materializers indexed `rows[0]`/`value_fields[0]` unconditionally, so an empty result
  set or a plan missing its role fields raised `IndexError`. All chart types now degrade
  to an empty chart (covered by a fuzz test over every chart type).
- **`render_body` is genuinely "always safe"** — it crashed on `charts=None` despite the
  contract; guarded.
- **Schema discovery `IndexError`** — asset discovery indexed the name-filtered database
  list with original-list positions, crashing when a database had an empty name.
- **Dashboard generation no longer crashes on partial model output** — `QuerySource`/
  `ParamSpec.from_dict` tolerate a missing source `id`/`sql` or param `name` (an empty
  SQL now surfaces as a normal, repairable validation error).
- **CLI backup output** tolerates a partial result instead of `KeyError`.
- **Attribute-injection fix** — the dashboard table header escaped `&<>` but not quotes,
  so a column name containing `"` could break out of a `data-col="…"` attribute.
- **Import manifest atomic write** — a crash mid-write no longer truncates `manifest.json`
  and orphans the imported tables.
- **MCP `_ToolContext` is thread-safe** — concurrent `ask` requests for one connection no
  longer race the adapter cache.

### Performance

- **Foreign-key persistence is O(n) not O(n²)** — `JoinCatalogStore.add_many` does one
  load+save for the whole batch instead of per edge.
- **Heatmap rendering is O(n) not O(n²)** — sorted axis index map (also makes the axis
  order deterministic across refreshes).
- **QueryHistoryStore** mirrors history in memory, so the workbench no longer re-reads and
  re-parses the capped file on every query.
- Single-pass schema-asset summary.

## [0.9.15] — 2026-06-26

### Performance

- **Dashboard schema grounding cached per connection** — generating a dashboard gathers
  schema context for the builder (~60 read queries: list/describe tables plus a `DISTINCT`
  per low-cardinality text column). This is now cached per connection for the session, so
  iterating on several dashboards in a row no longer re-introspects the database each time.
  The cache is invalidated when a connection is saved or deleted.

## [0.9.14] — 2026-06-25

### Added

- **Cross-filtering** — click a category on any dashboard chart to filter the whole board
  to that value (the classic drill-down). The matching filter is found automatically by
  which one's options contain the clicked value — no setup. Click the same value again to
  clear it.
- **System-driven chart sizing + responsive layout** — the system now sizes charts by type
  AND real data instead of relying on the model's guesses: type-aware heights (clamped so a
  pie can't be 600px), natural widths / "size" classes, and client-side auto-grow (a
  horizontal bar grows with its bar count; a crowded category axis rotates labels and gains
  a scroll zoom). Rows stack full-width on narrow screens.
- **Polished components** — KPI cards (label-over-value, optional %-delta + sparkline,
  number formatting), sortable/zebra-striped tables, refined cards and filters, a cohesive
  chart colour palette.

### Changed

- The builder is steered toward denser layouts (charts side-by-side or grouped into tabs
  rather than a tall single column) and toward summary tiles when asked.
- Dashboard recipe execution now also runs through the EXPLAIN cost gate (when the
  connection policy sets a row limit), so an over-large query surfaces as a tile error
  instead of a heavy scan.

### Fixed

- Teardown safety: the dashboard's query thread pool is now drained on close (no
  use-after-free if you close while tiles are loading).
- A chart no longer renders blank after a no-data→data refresh (stale echarts instance is
  disposed); horizontal-bar fallback/sizing reads the right axis; union/join recipes keep
  columns that only later rows add; scatter reconcile can't collapse x and y onto one
  column; multi-select filter validation, low-cardinality value grounding on MySQL, and
  light-theme table striping are all corrected.

### Added

- Dashboards aren't chart-only: a text/markdown tile now renders GFM pipe tables
  (`| A | B |`) as real tables (with inline markdown in cells), so composed summaries and
  key-findings tables display alongside charts and recipe-driven data tables. The builder
  is steered to use table/text tiles when a chart isn't the right fit.

### Fixed

- Chart-plan reconciliation is now chart-type-aware and guarantees the category/value
  fields for category-type charts (bar/line/pie/…): a plan that omits or misnames those
  fields still renders, with the mapping derived from the actual result columns.

## [0.9.12] — 2026-06-25

### Fixed

- **Charts render reliably from real data.** The top cause of a blank/garbage chart was
  the chart_plan (column→role mapping) drifting from the SQL it was paired with — naming
  columns the query doesn't return, which silently produced all-"—"/0 charts. Two layers
  now prevent this: the runtime reconciles the plan against the ACTUAL result columns
  (auto-deriving category/value/x/y from the real data when a field is missing), and the
  page falls back to assembling a chart client-side from the returned rows if the
  server-side spec is still absent. A chart only shows "no data" when there genuinely is none.

### Changed

- The left schema/assets sidebar is hidden in the Dashboards mode (it's irrelevant there),
  giving the board full width; it returns when switching back to Assistant/Workbench.

## [0.9.11] — 2026-06-24

### Fixed

- Correctness pass over the AI-dashboard subsystem (from an adversarial review):
  - **Join data loss** — combining multiple sources with `mode:"join"` dropped rows
    whose join key was NULL/missing into a single overwriting bucket; keyless rows are
    now dropped cleanly and real keys join correctly.
  - **Filter validation gap** — a multi-select filter mistakenly written in scalar form
    (`col = :p`) passed build-time validation (which used the default) but produced
    invalid SQL when 2+ values were selected; validation now exercises the `IN(...)` list
    form so the mistake is caught and self-corrected at build time.
  - **Table sorting** now uses a consistent comparator (numeric columns numerically,
    others lexically, NULLs always last) instead of mixed-type comparison.
  - Switching dashboard tabs no longer re-renders already-current tiles, and a tab opened
    after applying a filter correctly refreshes with the new filter values.
  - The dashboard's worker thread pool is owned by the view (not recreated per render),
    removing a use-after-free window when refining while a query was still loading.

### Added

- Applying a filter now shows a loading overlay on each tile (a spinner over the
  existing chart/KPI/table, which stays visible) until the refreshed data lands.

## [0.9.9] — 2026-06-24

### Added

- **Responsive dashboard loading** — the dashboard's data bridge is now asynchronous:
  each tile's query runs on a worker thread instead of back-to-back on the GUI thread,
  so opening a multi-tile board no longer freezes the app. Tiles show a loading spinner
  while their data is in flight (and keep their previous content during a filter refresh).

### Fixed

- Filters with a dynamic default (e.g. start-of-month) now render the resolved concrete
  value, so the initial filter condition is visible and editable rather than a blank box.

## [0.9.8] — 2026-06-24

### Added

- **Richer dashboard components** — KPI tiles gain number formatting (int/float/percent/
  currency) and an optional trend mode that shows the latest value, its %-change vs the
  previous point, and an inline sparkline; tables auto-format and right-align numeric
  columns and make every header click-to-sort; multi-select filters get one-click
  "All / Clear" actions. All still system-rendered from the declarative layout.

### Fixed

- CI was silently red since the 0.9.6 dashboard work: GUI tests created real Chromium
  WebEngine views that abort during teardown under offscreen Linux CI (exit 134), even
  though every test passed. The test harness now neutralizes all WebEngine-creation
  paths and exits cleanly on a green run, so CI reflects reality again.

## [0.9.7] — 2026-06-24

### Added

- **AI interactive dashboards** — a dedicated builder agent (separate from the chart agent and
  the Ask orchestrator) turns a conversation's analysis into an interactive dashboard. It emits
  a declarative **component tree** — nestable rows / columns / grids / sections / tabs / cards
  holding chart · KPI · table · text/markdown tiles — and the system renders it deterministically
  (themed, responsive); the model never writes HTML. Filters are auto-generated from the recipes'
  parameters as compact, collapsible multi-selects. Refine the board across turns in natural
  language, and choose which configured model generates it.
- **Dashboards gallery** — generated dashboards are saved and reopenable: the Dashboards mode is
  now a gallery of saved AI dashboards (open to view + chat-refine, or delete). The static basic
  board is retired.

### Changed

- AI-dashboard recipes are grounded in the real database: the builder is given the actual schema,
  column types, and distinct values for low-cardinality columns, plus the SQL dialect — so filter
  options match the data instead of being guessed.

### Fixed

- AI-dashboard recipes are validated by EXPLAIN against the real database at build time, and the
  builder self-corrects (feeds the DB error back to the model) — eliminating dashboards that
  rendered all "no data" from invented columns/functions or filter values that matched nothing.
- A filter change can no longer error a tile: empty results render as "no data", parameter binding
  tolerates empty/edge values, and numeric/date cells survive the bridge as JSON-native types so
  KPIs format correctly.
- Dashboard colours are injected from the live app theme (no hardcoded palette), and a recipe
  shared by several tiles runs its SQL once per refresh.
- Answers no longer leak the raw `{"action":"finish","answer":…}` JSON object on the native
  tool-calling path.

## [0.9.6] — 2026-06-23

### Added

- **Dashboards & saved questions** — pin a chart from any answer as a *saved question*: it
  captures the question, the SQL that produced its rows, and the chart plan (field→role
  mapping), so it can be re-run later and redrawn from fresh data without another model call.
  Compose saved questions into multiple dashboards; each board is a 12-column grid where tiles
  drag to reorder and resize their footprint (live reflow + animation), titles edit inline,
  and tiles refresh on demand on a background thread. Turns "ask once" into "keep watching".
- **Background Excel import with progress** — large workbook imports now run off the UI thread
  behind a progress indicator, so the app stays responsive.

### Fixed

- Chart provenance is recorded only when a chart's rows came from a query (not inline/computed
  data), so a refreshed tile re-runs the exact SQL that produced it; schema drift is refused
  rather than silently drawing a zeroed chart over the last good snapshot.

## [0.9.5] — 2026-06-23

### Added

- **More model API protocols** — `anthropic` (Claude Messages API) and `openai_responses`
  (OpenAI Responses API) alongside OpenAI-compatible, selectable in Settings → Models and
  `dbaide model add --provider`.
- **Excel import — management & shaping** — drag-and-drop files onto the staging dialog;
  per-sheet selection (import only the sheets you want) and a header-anchor picker (row +
  start column) with validation; re-import/refresh a workbook from its source file; a
  read-only data preview of the resulting tables; auto-skip of preamble rows, vertical-merge
  fill, and per-sheet fault isolation.

### Fixed

- **Streamed answer height** — the live answer now grows with content (was clipped to one
  line during streaming, with trailing blank space after) by streaming into a QTextEdit and
  measuring true pixel height.
- Excel import robustness: atomic writes, bounded grid for inflated dimensions, header-extent
  column span, big-integer/CSV-newline handling, collection-name safety.

## [0.9.4] — 2026-06-23

### Added

- **Two more model API protocols** — besides OpenAI-compatible, you can now pick `anthropic`
  (Claude Messages API: `/v1/messages`, `x-api-key` + `anthropic-version`, system hoisted to the
  top-level field, tool calls via `tool_use` blocks, SSE streaming) and `openai_responses`
  (OpenAI Responses API: `/v1/responses`, `instructions` + `input`, `function_call` items) in
  Settings → Models or via `dbaide model add --provider`. Both are raw-HTTP `LLMClient`
  subclasses — no SDK dependency, consistent with the existing client — and support native
  tool calling, JSON decisions, and streaming. (DeepSeek, Kimi, Qwen, GLM, Ollama, and Azure
  already work through the OpenAI-compatible option; `anthropic` and `openai_responses` are the
  genuinely different wire formats.)

- **Import CSV/Excel** — `dbaide ingest <file…>` imports one or more `.csv`/`.tsv`/`.xlsx`/
  `.xlsm` files into a local SQLite connection you can immediately ask/query, registered
  like any other connection. Phase 1 handles the common case — one clean rectangular table
  per sheet (header on the first non-empty row): type inference (INTEGER/REAL/TEXT, with
  leading-zero codes preserved as text), CJK-safe column sanitization + de-duplication,
  multi-sheet → multiple tables, multi-file with collision-safe table names, hidden sheets
  skipped, and a `manifest.json` recording provenance. The import runs in one transaction
  and leaves no partial database on failure. `.xlsx` needs the optional `openpyxl`
  (`pip install dbaide[imports]`); CSV needs nothing.
- **Managed Excel collections in the desktop** — Settings → Connections → **New** now asks
  whether to create a database connection or an Excel/CSV one. Choosing Excel/CSV opens a
  staging dialog: name the connection, add one or more files, and rename each resulting table
  before creating. Selecting an existing collection
  shows a workbook manager (right pane) to **add**, **rename**, or **remove** workbooks —
  adding a file whose name matches an existing one offers a quick overwrite (delete-then-add),
  and removing the last workbook deletes the connection. Any change re-projects the catalog
  (fast, no LLM) so the schema tree stays current. A collection is an ordinary read-only
  `sqlite` connection under the hood, so the assistant, charts and read-only safety all work
  unchanged.
- **Smarter sheet reading + header picker** — the importer now finds the real table inside a
  sheet instead of assuming row 1: it skips title/metadata/blank preamble rows by detecting
  where the column types stabilize, and fills vertically-merged grouping columns downward
  (so `GROUP BY` works) while leaving genuinely-missing cells null. Reading is **per-sheet**:
  a sheet that fails to parse is skipped (and reported) instead of failing the whole workbook.
  The staging dialog's **Header…** button opens a grid preview where you click the top-left
  header cell — both the header **row and start column** are honoured (columns to the left are
  dropped) and the columns below are matched automatically. The chosen shape (header row, data
  bbox, filled columns) is recorded in the manifest. Plain files (header on row 1) are unchanged.

## [0.9.3] — 2026-06-22

### Added

- **Copy whole-turn trace** — the trace drawer header now has a copy button that copies the
  entire turn's trace (every step + SQL), filling the gap between copying a single step
  (the detail tray) and the whole session. The capability existed on the inline timeline
  but its header was hidden in the drawer.

### Fixed

- **Agent task list (agenda) never appeared** — `update_agenda`'s `items` were advertised
  to the model as a bare `array of object` with the field names buried in a prose
  description, so a native tool-calling model guessed them — sending `{"task": …,
  "status": "待开始"}` instead of `{"title": …, "status": "pending"}`. Those items then
  failed `agenda_from_dict`'s `title` check, the tool returned an empty agenda ("no
  tasks"), and the panel stayed hidden. The native tool schema now carries a structured
  item schema (`title` required, `status`/`kind` enums) via a new `items_schema` on the
  tool spec, so the model is *told* the fields. An audit fixed the same underspecification
  on the other model-facing tools: `annotate_object.scope` was a `column|table|database`
  choice in prose (now a real `enum`), and `retrieve_schema_context.scope` was an
  undocumented object (now carries `{databases, tables}` properties). The tool→function
  converter now passes `enum`, array `items_schema`, and nested object `properties` through
  to the native tool schema.
- **MCP `column_stats` tool** — the `metrics` array advertised its valid values
  (`min`/`max`/`null_rate`/…) only in the description, not as an item `enum`, so an MCP
  client could send unsupported metric names. It now carries a real `enum` kept in sync
  with the metrics the tool can actually compute. (Audit confirmed every advertised MCP
  tool maps 1:1 to a handler and all input schemas are well-formed.)
- **Agent task list (agenda)** — the conversation's agenda panel showed during a live run
  but vanished once the turn finalized or the chat was reopened. The tool layer flattened
  the tool result to a 200-char `output_preview` string in the persisted trace, so the
  structured task list was lost and the panel's parser found nothing. The agenda now rides
  in the trace event's `metadata` (carried via a new opt-in `ToolResult.meta`), survives
  persistence/reload, and the parser reads it from `result_data` (live) or `metadata`
  (persisted).

## [0.9.2] — 2026-06-22

### Changed

- **UI polish** — motion + state refinements with no layout changes. Keyboard focus is
  visible again (buttons' `:focus-visible` and the "soft" combo use the accent; checkbox
  /radio gained an accent focus border). The 对话/工作台 mode switch slides an animated
  selection pill between tabs; switching tabs fades the incoming page in (skipped for
  WebEngine-hosting pages, which an opacity effect would black out); dropdown menus fade
  in on open; the main window fades in on launch (window-level opacity, safe over
  WebEngine); dialogs fade in on open (guarded so they can never get stuck transparent).
  The SQL editor brightens the current line's number in the gutter.

### Fixed

- **Settings** — the Connections "Import" action was clipped to "Impor" by a fixed button
  width sized for the shorter "New" label; it now auto-fits its label (found in a live
  UI walkthrough).

## [0.9.1] — 2026-06-22

### Security

- **WebEngine answer/markdown pages** — untrusted content (model markdown + DB-derived
  chart data) was embedded into an inline `<script>` via `json.dumps`, which does not
  escape `/`; a value containing `</script>` could break out and execute arbitrary JS
  in the page. All such payloads now go through a `<script>`-safe encoder
  (`<`, `>`, `&`, U+2028/U+2029 → `\uXXXX`).

### Fixed

- **Streaming** — the final answer no longer duplicates when a decision retries
  (the answer field was re-streamed by a fresh streamer each attempt); a mid-stream
  transport failure no longer re-emits the full text on top of partial chunks.
- **Trace UI** — fixed a use-after-delete crash in the deferred scroll callback
  (target card/panel could be rebuilt before it fired); a full rebuild (e.g. a step
  gaining its first sub-step) now preserves the reader's scroll position; the timeline
  connector to newly appended steps is no longer dropped; `ingest` tolerates a
  non-numeric step/timestamp/duration in a corrupted persisted trace.
- **Multi-run sessions** — a new chat whose server `session_id` collided with an
  already-open slot no longer orphans the conversation (slot remap is collision-safe,
  live state wins); a clarification reply queued at capacity now resumes with the
  correct `session_id` instead of an empty one.
- **Export / dialogs** — a failed export file write now alerts the user instead of
  failing silently; the "copied" reset and the HTML-export dialog no longer touch a
  deleted widget after close; the save dialog pre-fills an extension-less filename
  instead of treating it as a directory; non-native file dialogs are released after use.
- **Icons** — an unknown/typo icon name falls back to a blank glyph instead of
  crashing the SVG render.

### Changed

- **Conversation state layer** — unified per-slot state (one `ConversationSlotState`
  per slot in `ConversationRunState`) with single rename/discard entry points that keep
  the run-state and the ask-tab view in lockstep; removed the now-dead per-field
  mapping facades and window-level slot aliases.

## [0.9.0] — 2026-06-12

### Added

- **Chart agent coverage** — full materialization and ECharts paths for all 22 chart
  types (heatmap, sankey, treemap, gauge, boxplot, waterfall, etc.) with expanded
  unit tests.
- **Dialog layout helpers** — shared `configure_compact_field`, `compact_field_column`,
  and `dialog_action_row` / `dialog_action_column` for consistent form control sizing.

### Changed

- **ChromeDialog sizing** — after macOS safe-area insets apply, dialogs auto-sync
  minimum height from layout `sizeHint()` so content is not clipped or overlapped.

### Fixed

- **Chart pipeline** — heatmap/sankey duplicate-cell aggregation; gauge progress arc
  and target label; funnel `sort_order`; scatter/bubble validation and empty-series
  render; multi-axis `"right"` hint mapping for two-series charts.
- **Backup dialog** — format/batch controls no longer squashed; action button no longer
  overlaps inputs after safe-area layout.
- **Dialog layouts** — backup, text input, message/choice, note editor, connection,
  build assets, join editor, cell value, and HTML export sidebar: fixed-height fields
  and separated action rows.
- **Desktop chrome** — mode switch (对话/工作台) clipping; workbench tab bar black
  native edge; panel tab max-width.
- **CI** — GUI tests stub WebEngine so pytest passes on Linux headless runners.

## [0.8.0] — 2026-06-12

### Added

- **Chart tools dialog** — turn footer **More → Chart tools…** opens an interactive
  viewer (zoom slider + wheel) without hijacking conversation scroll.
- **Themed file dialogs** — save/export paths use app-styled `QFileDialog` wrappers
  for consistent light/dark chrome.

### Changed

- **In-chat charts** — default to read-only (`chartInteractive: false`): no
  `dataZoom` sliders or wheel zoom in the message list; tooltips still work.
- **Exported HTML** — same read-only chart mode as the chat (portable CDN bundle,
  optional padding via the export dialog).
- **Run / UI state** — background work and conversation run state refactored
  (`ui_state.py`, `service_payloads.py`) for clearer slot sync while switching
  sessions.

### Fixed

- **Chart interaction dialog** — WebEngine now passes `base_url` so bundled
  ECharts/marked scripts load (fixes “ECharts failed to load”).
- **Export preview** — aligned with the shared `build_answer_document_html` path.

## [0.7.0] — 2026-06-12

### Added

- **Unified answer documents** — Markdown + inline ECharts compose into a single
  WebEngine page (`compose` / `answer_page` / `AnswerDocumentBlock`); session
  restore keeps chart rendering.
- **HTML export** — merged copy/save into an export dialog with configurable
  padding, live preview, and shared render path with the in-app answer view.
- **Ask agenda / task list** — in-run task list tools and trace integration for
  multi-step planning (`agenda` module replaces deprecated intent routing).
- **Step budget defaults** — default agent max steps raised to 128 (cap 256);
  timeline step count and loop budget share one source of truth.
- **Settings** — `session_uncompressed_turns` controls how many recent turns stay
  full-fidelity in session memory.

### Changed

- **Trace UI** — incremental timeline rendering (structure fingerprint) reduces
  flicker during live runs and session bulk load.
- **Agent loop** — removed schema prefetching from decision prompts; simplified
  loop state.
- **UI polish** — standardized 28px control heights, sidebar/session list loading
  states, theme token cleanup, localized turn summary lines.

### Fixed

- **Session restore charts** — chart answers no longer downgrade to plain-text
  fast path during bulk load.
- **Chart styling** — transparent ECharts canvas background blends with the chat
  surface.

## [0.6.0] — 2026-06-19

### Added

- **ECharts chart rendering** — charts render via Qt WebEngine + Apache ECharts (auto
  dual-axis for mixed magnitudes, compact date labels, dataZoom, ResizeObserver).
- **WebEngine startup init** — import WebEngine before `QApplication` so frozen and
  dev builds can render charts reliably.
- **PyInstaller WebEngine bundling** — GUI spec includes `QtWebEngineCore/Widgets`
  and related modules; `requirements-gui.txt` lists `PyQt6-WebEngine`.

### Changed

- **Chart block** — simplified to a WebEngine host; option generation lives in
  `dbaide/charts/echarts.py` (GUI-free).
- **Agent memory / tool output** — `latest_result_limit` accepts `0` (unlimited);
  `_format_tool_result` supports a configurable character limit; `retrieve_turn`
  exposes additional memory fields.

### Fixed

- **Startup SSL CA check** — probe runs in a background thread; UI no longer blocks
  for up to 5s on launch.
- **Streaming answers** — `flush_final` emits any tail missed during JSON streaming;
  `complete_turn` merges streamed vs authoritative text and force-rebuilds markdown
  with deferred height sync so long answers are not clipped.
- **Frozen GUI charts (WebEngine)** — PyInstaller spec uses `collect_all` for WebEngine
  binaries/resources, pins aligned `PyQt6`/`PyQt6-WebEngine` versions, disables strip on
  Qt libs; CI runs `--verify-webengine` on the built bundle.

## [0.3.0] — 2026-06-16

### Added

- **Settings → Integrations** — help button (circle ?) beside「全部安装」opens an MCP
  integration guide: what it does, prerequisites (connections, models, assets),
  example prompts for AI tools, and `conn` / `database` tips (EN/ZH).

## [0.2.18] — 2026-06-16

### Fixed

- **Settings → Integrations** — PyInstaller release builds now bundle all
  `tool_icons` assets (Claude, Cursor, Windsurf, …); icons were blank in installed
  apps because only `app_icon.png` was previously included in the frozen bundle.

## [0.2.5] — 2026-06-14

### Changed

- **Ask answer area** — removed inline SQL blocks and copy/open SQL shortcuts; SQL remains
  in Trace for developers.
- **Charts** — legend, tooltips, combo/dual-axis, stacked area (filled stacked bars),
  multi-series metadata; ChartAgent prompt prefers multiple charts for complex
  multi-metric questions.
- **Chart embeds** — canonical placeholder is `{{chart:N}}` (from `embed_markdown`);
  charts render only when referenced in the answer (no orphan append).
- **SQL history** — every successful `execute_sql` / `execute_readonly_sql` appends to
  `executed_sqls` on the turn (with optional `purpose` tag, ≤20 chars). `selected_sql`
  is the last executed query for backward compatibility; exploration vs final is no
  longer a separate channel.

### Fixed

- **Combo charts** — left/right bar series attach to the correct Y axis; all-right-axis
  combos no longer show an empty left axis; right axis title renders on the chart.
- **ChartAgent** — scalar `series_types` / `series_axes` from the LLM apply to all
  series (not only the first).
- **Trace** — Chart Agent sub-steps show as「Chart planning」instead of raw `chart_agent`.
- **CI** — GUI session tests tear down `AskTab` widgets to avoid offscreen Qt aborts in
  the full pytest run; tool-spec test updated for unified SQL history.

## [0.2.4] — 2026-06-13

### Changed

- **TopBar update control** — matches Settings → About: external-link icon with
  「有 vX 可更新」 ghost action; sits to the right of the connection selector (replaces
  the blue download pill).

## [0.2.3] — 2026-06-13

### Added

- **Asset status bar** — persistent schema summary above the tree (尚无资产 / 基础结构 /
  已采样 / 部分采样 / 增强过期 / 构建有错误) with table/column/sample counts.
- **Auto base build on new connection** — projects catalog on first connect; toast
  「基础结构已初始化」.
- **Build progress for enrich/sampling** — context-menu enrichment uses the same
  live progress card as manual builds.
- **GitHub release check** — fetches latest release on startup; TopBar update button
  (when a newer version exists); Settings → About shows latest release with download link.
- **`release_check` module** — semver compare, ahead-of-release / up-to-date states.

### Changed

- **Default build concurrency** — production `build_max_workers` **4**; Build Assets
  dialog reads effective policy (Settings → Resources respected).
- **Default max concurrent runs** — **6** (was 3).
- **Resource defaults** — verified end-to-end wiring; build dialog workers follow saved policy.

### Fixed

- **Build failure UX** — failed builds refresh from store instead of wiping the schema
  tree; asset summary shows errors when instance stats report failures.
- **Release check UI** — fixed stuck「正在检查…」by marshaling results to the main
  thread via Qt signal (not `QTimer` from a worker thread).
- **About latest version** — distinguishes up-to-date, update available, and
  ahead-of-release (dev builds).

## [0.2.2] — 2026-06-12

### Added

- **HTTPS certificate trust (`certifi`)** — LLM API calls verify TLS against the
  bundled Mozilla CA bundle instead of relying on a broken local Python trust store.
- **Startup SSL check** — GUI warns once at launch if HTTPS verification to a public
  API host fails (proxy / corporate cert guidance).
- **Settings → Models** — note explaining HTTPS, certifi, and SSL troubleshooting.
- **Promo assets** — screenshot set under `docs/images/promo/` and `tools/shoot_promo.py`
  for capturing marketing images.

### Fixed

- **LLM `CERTIFICATE_VERIFY_FAILED`** — frequent macOS / pyenv SSL errors when
  calling OpenAI-compatible endpoints; PyInstaller bundles now include certifi data.
- **SSL error hints** — dedicated `error.llm.ssl` message when certificate verification
  fails (distinct from generic network errors).

## [0.2.1] — 2026-06-12

### Added

- **Settings → About** — version, developer, license, and project links (GitHub,
  releases, issues, README).
- **Chart axis label layout** — compact ISO date formatting, automatic label tilt
  for dense categories, hover tooltips on line/bar charts.

### Changed

- **Chart Agent prompts** — time-series guidance (line/area, `category_asc`, sensible
  `limit`, date bucketing in SQL).
- **Workflow prelude progress** — environment check and planning stream to the live
  trace before the agent loop starts.

### Fixed

- **Trace summary at run start** — no longer shows「空闲 / Idle」while the connection
  environment check is running.
- **Trace detail「复制原始数据」** — copy button layout and full step JSON export.
- **Chart x-axis labels** — disabled Qt `truncateLabels` that cropped dates to
  `202…`; rotated labels when categories are dense.

## [0.2.0] — 2026-06-12

### Added

- **Inline chart embeds** — charts render inside the answer markdown at
  `{{chart:chart:N}}` or `![caption](chart:N)` placeholders; multiple charts per
  reply; unreferenced charts still append at the end for backward compatibility.
- **`embed_markdown` in `render_chart`** — tool output includes a ready-to-paste
  placeholder for the finish answer.

### Changed

- **Chart Agent (LLM-only planning)** — chart type and column mapping must come from
  the Chart Agent LLM; removed heuristic chart-type selection and `_infer_fields`
  fallback (missing/invalid fields raise a retryable tool error instead).
- **Agent prompts** — main agent embeds charts inline in `finish` answers instead of
  prose-only summaries at the end.

### Fixed

- **`dbaide.__version__`** — synced with `pyproject.toml` (was stale at `0.0.6`).

## [0.1.10] — 2026-06-12

### Added

- **Chart rendering** — `render_chart` tool with a dedicated Chart Agent for type/field
  mapping; Qt Charts UI (`PyQt6-Charts`) for bar, horizontal bar, line, pie, donut,
  stacked bar, and scatter plots; session persistence for chart specs.
- **Copy answer** — one-click copy of the agent's full markdown reply in the Ask tab.

### Changed

- **Agent loop termination** — only `action=finish` (or `ask_user`) ends a run; successful
  `execute_sql` no longer auto-completes the turn.
- **App icon** — restore bundled PNG logo in the title bar and window icon.
- **Ask action bar** — ghost-style buttons with icons for copy SQL / open in SQL / copy CLI.

## [0.1.9] — 2026-06-12

### Changed

- **Linux release builds** — CI now runs on `ubuntu-22.04` (glibc 2.35); tarballs
  require **Ubuntu 22.04+** (20.04 is no longer supported).

## [0.1.8] — 2026-06-12

### Added

- **Schema build progress card** — compact spinner + `done/total` counter (no bar);
  current table shown as a detail line during asset builds.
- **Live schema tree updates** — tables appear incrementally while assets build,
  without wiping manual expand/collapse state.
- **i18n for build progress** — English / 简体中文 strings for build-phase titles,
  sidebar schema heading, and localized asset-builder status messages.

### Changed

- **Build progress UX** — remove duplicate loading row in the schema tree while the
  progress card is visible; debounce rapid progress/tree refreshes to reduce flicker.

### Fixed

- **Build progress crash** — fix `NameError` in debounced progress flush (`node_id`).
- **Connection switch during build** — cancel stale debounced schema refreshes so
  another connection's tree is not applied to the current view.
- **Asset builder progress events** — emit per-database table counts and current table
  for accurate GUI progress tracking.


### Changed

- **Linux minimum support Ubuntu 20.04 LTS** — release builds run on `ubuntu-20.04`
  (glibc 2.31); tarballs run on 20.04+ but not 18.04. Enable `universe` for
  `libxcb-cursor0` on 20.04 during CI/local builds.

## [0.1.6] — 2026-06-11

### Fixed

- **Linux CI build** — correct Ubuntu package name `libxcb-xkb1` (was invalid
  `libxcb-xkb0`); centralise xcb apt deps in `packaging/linux/apt-xcb-deps.txt`.

## [0.1.5] — 2026-06-11

### Fixed

- **Linux startup crash (`xcb` plugin)** — bundle `libxcb-cursor` and related xcb/xkb
  runtime libraries into the PyInstaller folder; set `LD_LIBRARY_PATH` via a runtime
  hook so Ubuntu users no longer need manual `apt install libxcb-cursor0`.

### Added

- **`packaging/linux/bundle_qt_runtime_libs.sh`** — CI/local build step to vendor Qt
  xcb deps; **`INSTALL.txt`** included in the Linux `.tar.gz`.
- **README / PACKAGING** — document Ubuntu `apt` fallback for source installs and
  older tarballs.

## [0.1.4] — 2026-06-11

### Changed

- **Windows title bar** — drop the v0.1.3 custom frameless caption bar; restore
  native minimize / maximize / close controls.

### Fixed

- **Windows title bar + TopBar theme** — DWM immersive dark/light mode tints the
  native caption strip (background, border, title text) to match the app palette;
  in-app `#topBar` background follows theme via global QSS.

## [0.1.3] — 2026-06-11

### Added

- **Windows custom caption bar** — frameless title strip with theme-aware gray
  minimize / maximize / close buttons (main window + all dialogs); native resize
  and drag-to-move preserved.

### Fixed

- **Windows title bar theme** — DWM immersive dark/light mode tints the caption
  strip to match the app palette when the custom caption is not active.
- **TopBar background** — `#topBar` / `#windowsCaptionBar` styled via global QSS
  so the header tracks light/dark theme on every platform.

## [0.1.2] — 2026-06-11

### Fixed

- **Windows title-bar ghosting** — disable expanded client area and DWM caption
  tint on Windows/Linux (macOS only); dialogs and main TopBar no longer double-draw
  over the system caption strip.
- **Windows DWM** — border colour only; caption/text colours removed to prevent
  overlap artefacts on small dialogs.

## [0.1.1] — 2026-06-11

### Added

- **`window_chrome` module** — shared native title-bar integration for the main
  window and every dialog (`ChromeDialog` base class).

### Fixed

- **Windows side gutters** — themed window palette, DWM border/caption colours, and
  horizontal safe-area cancellation so content is edge-to-edge.
- **Dialog title bars inconsistent with main window** — settings, connections, build
  assets, joins, alerts, and other popups now use the same integrated chrome.
- **Linux / future Qt** — expanded client area enabled whenever the Qt 6.9 API is
  available; all platforms still get themed window backgrounds.

## [0.1.0] — 2026-06-11

### Added

- **Integrated native title bar (macOS / Windows)** — Qt 6.9 expanded client area
  blends the app header with the system window chrome while keeping native close,
  minimize, and maximize controls.

### Fixed

- **Oversized gap under the title bar** — safe-area top inset was applied twice
  (once by Qt on the central widget, once in TopBar); header content now sits
  directly below the traffic lights / caption buttons.

## [0.0.9] — 2026-06-11

### Added

- **README.zh-CN.md** — full Simplified Chinese readme; install instructions (incl.
  macOS Privacy & Security) moved to the top of both readmes.
- **Windows desktop shortcut** — MSI installer now places a shortcut on the Desktop.
- **Linux `.desktop` file** — bundled in the `.tar.gz` for manual menu integration.
- **Windows release smoke test** — CI verifies the frozen EXE stays running.

### Fixed

- **Windows/Linux letter keys hijacked by toolbar** — mode-switch and chrome buttons
  no longer steal Alt+letter mnemonics; composer refocuses after Alt on Windows/Linux.
- **Light theme faint borders** — retuned `BORDER` / `BORDER_SOFT` tokens for visible
  but not heavy edges.
- **Combo dropdown black corners** — opaque popup styling (same approach as menus);
  `BuildAssetsDialog` uses the shared `Combo` widget.

## [0.0.8] — 2026-06-10

### Fixed

- **Desktop release builds exit immediately on launch** — the PyInstaller entry
  script (`launcher.py`) defined `main()` but never called it, so macOS, Windows,
  and Linux bundles started and quit silently (`console=False`). Added the
  standard `if __name__ == "__main__"` guard.
- **Linux release smoke test false positive** — CI treated an instant exit (code 0)
  as a healthy launch; only a process still running at timeout (124) passes now.

## [0.0.7] — 2026-06-09

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

[Unreleased]: https://github.com/W1412X/dbaide/compare/v0.9.1...HEAD
[0.9.1]: https://github.com/W1412X/dbaide/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/W1412X/dbaide/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/W1412X/dbaide/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/W1412X/dbaide/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/W1412X/dbaide/compare/v0.3.0...v0.6.0
[0.3.0]: https://github.com/W1412X/dbaide/compare/v0.2.18...v0.3.0
[0.2.18]: https://github.com/W1412X/dbaide/compare/v0.2.17...v0.2.18
[0.2.5]: https://github.com/W1412X/dbaide/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/W1412X/dbaide/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/W1412X/dbaide/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/W1412X/dbaide/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/W1412X/dbaide/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/W1412X/dbaide/compare/v0.1.10...v0.2.0
[0.1.10]: https://github.com/W1412X/dbaide/compare/v0.1.9...v0.1.10
[0.1.9]: https://github.com/W1412X/dbaide/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/W1412X/dbaide/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/W1412X/dbaide/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/W1412X/dbaide/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/W1412X/dbaide/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/W1412X/dbaide/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/W1412X/dbaide/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/W1412X/dbaide/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/W1412X/dbaide/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/W1412X/dbaide/compare/v0.0.9...v0.1.0
[0.0.9]: https://github.com/W1412X/dbaide/compare/v0.0.8...v0.0.9
[0.0.8]: https://github.com/W1412X/dbaide/compare/v0.0.7...v0.0.8
[0.0.7]: https://github.com/W1412X/dbaide/compare/v0.0.6...v0.0.7
[0.0.6]: https://github.com/W1412X/dbaide/compare/v0.0.5...v0.0.6
[0.0.5]: https://github.com/W1412X/dbaide/compare/v0.0.4...v0.0.5
[0.0.4]: https://github.com/W1412X/dbaide/compare/v0.0.3...v0.0.4
[0.0.3]: https://github.com/W1412X/dbaide/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/W1412X/dbaide/releases/tag/v0.0.2
