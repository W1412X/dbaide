# DBAide Design

## Positioning

DBAide is a CLI-first database assistant. It is not a pure SQL generator. The assistant can inspect schema, profile data, generate safe queries, execute read-only SQL, diagnose SQL, and explain results.

## Lessons Taken From AskDB

AskDB has strong safety and correctness ideas:

- SchemaLink avoids giving the model the entire database at once.
- SQL validation blocks multi-statement and write SQL.
- Real database `EXPLAIN` catches syntax and object errors before execution.
- Read-only execution, timeout, and row limits reduce blast radius.
- Step logs make the workflow inspectable.

DBAide keeps these ideas but removes first-version weight:

- No embedding initialization.
- No semantic vector search.
- No Web/SSE workflow.
- No multi-intent DAG executor.
- No heavy repository/checkpoint persistence.

The replacement is a smaller progressive-disclosure loop that is easier to reason about in a CLI.

## Progressive Disclosure

DBAide initializes offline assets first, then reveals database information as a directory tree:

```text
L0 instance/connection
L1 database/schema under an instance
L2 table list and coarse table metadata under a database/schema
L3 columns under a table
L4 column profiles and samples
L5 explain plans, execution evidence, and result interpretation
```

The context is stored in `DisclosureContext`. Paths are represented as
`instance.database.table.column` when all levels are known. Tools update the
context after every schema/profile/query call. The assistant receives only the
context it has earned through tool calls.

Multiple configured connections are treated as multiple instances. Fan-out
queries can inspect several instances independently, but cross-instance joins
are intentionally not attempted.

## Offline Asset Initialization

`connect add` is not only a config write. By default it runs an initialization
workflow. This is the lightweight version of AskDB-style initialize assets:
there is no embedding model and no vector index, but the hierarchical JSON
documents are still mandatory because they are the primary schema-linking
surface.

```text
connect add
  -> test instance
  -> list databases/schemas
  -> list tables per database
  -> describe every table
  -> sample rows
  -> profile columns according to policy
  -> write one document per column
  -> synthesize table documents from column documents
  -> synthesize database documents from table documents
  -> synthesize instance document from database documents
```

The default profiling policy is `auto`: every column gets a document, but heavy
profile queries are only run for columns that are likely useful for schema
linking and SQL generation: primary keys, indexed columns, `*_id` link
candidates, time columns, status/category columns, and numeric measure columns.
Use `--profile-mode all` for full profiling, `--profile-mode none` for pure
structure assets, and `assets enrich` for selected table/column profiling.

Column documents are the most detailed asset. Profiled column documents include:

- physical metadata: type, nullability, default, primary key, index flag
- semantic role: identifier, time, numeric measure, categorical status, text, boolean
- quality: row count, null count, null rate, distinct count, distinct ratio
- range: min/max, temporal range, numeric average where available
- distribution: top values, top-value coverage, sample values, distinct truncation marker
- examples: bounded random samples and table sample rows
- usage hints: whether the column is suitable for filtering, grouping, joining, time aggregation, or measures

Unprofiled column documents still include physical metadata, inferred role,
semantic tags, and usage hints. Runtime tools can fall back to live exploration
or `assets enrich` can update selected columns without rebuilding the whole
instance.

Table documents are synthesized from column documents and include role indexes,
join hints, sample rows, source comments, and a natural-language table
description. Database documents are synthesized from table documents. Instance
documents are synthesized from database documents.

The asset tree is stored under `~/.dbaide/assets`:

```text
instances/<instance>/
  instance.json
  databases.json
  databases/<database>/
    database.json
    tables.json
    tables/<table>/
      table.json
      columns.json
      columns/<column>.json
```

Runtime tools prefer these assets. If an asset is missing, tools can still fall
back to the live adapter and record the newly disclosed layer in memory. This
preserves exploratory tools: assets guide the normal path, but the agent can
still inspect/profiling/query the live database when the stored documents are
not enough.

## Programmer Workflows

DBAide is optimized for developer tasks around an unfamiliar or changing
database:

- `tree`: quick directory-style view of instance/database/table/column assets
- `find`: locate where a concept probably lives, such as "user email" or "order amount"
- `ddl`: get copyable table DDL from the live adapter
- `relations`: inspect foreign keys and heuristic join hints
- `doc`: export a Markdown schema brief for code review or handoff
- `diff`: compare two asset schemas, such as dev vs prod
- `ask --no-execute`: generate validated SQL without running it

These commands use offline assets first and only call the live database when the
operation is inherently live, such as DDL retrieval, SQL validation, execution,
or explicit profile/enrich.

## Desktop GUI

The PyQt GUI is a thin interface over the same backend modules used by the CLI.
It does not introduce separate business logic. Long-running operations such as
asset builds, profiling, SQL execution, and LLM-backed asking run in a background
thread and stream progress into the active panel.

GUI tabs map to CLI workflows:

- Connections: `connect add`, `connect test`, asset build on save
- Assets: `assets build/status/show/enrich` plus hierarchical asset tree preview
- Explore: `tree`, `ddl`, `relations`, `doc`
- Find & Ask: `find`, `ask`, with an execution log panel showing selected instance, asset availability, plan notes, and disclosure steps
- SQL: `sql`, `diagnose`
- Diff: `diff`

SQLite, MySQL/MariaDB, PostgreSQL drivers and PyQt are default package
dependencies so a normal install can run both CLI and GUI without extra feature
flags.

## Candidate Discovery Without Vectors

Candidate tables and columns are selected by deterministic lightweight signals:

- table/column name match
- comments when available
- built-in Chinese/English alias dictionary
- primary key and index hints
- optional value profiling when a query needs enum or range information

This avoids startup cost and keeps the failure mode visible. If ambiguity remains, the CLI should ask the user or return the candidate list.

## Module Boundaries

```text
CLI
  parses commands and displays output

Agent
  routes task, plans disclosure, writes SQL, formats answers

Tools
  schema/profile/query/diagnose capabilities with clear inputs and outputs

Context
  records disclosed schema and execution evidence

Validation
  deterministic SQL and schema guards

Adapters
  database-specific metadata, EXPLAIN, read-only execute
```

Adapters are the only layer that knows database driver details.

## Safety Defaults

- Single SQL statement only.
- Only `SELECT`, `WITH`, and `EXPLAIN` are allowed.
- DDL/DML keywords are blocked.
- Dangerous functions and file/program access patterns are blocked.
- SQL gets a default limit unless explicitly bounded.
- Execution runs through `EXPLAIN` first.
- Adapters execute in read-only mode where supported.
- Result explanation is based on actual returned rows.

## Extensibility

Add a database:

1. Implement `DatabaseAdapter`.
2. Register it in `adapters/__init__.py`.
3. Add adapter tests using a disposable database.

Add a task:

1. Add a `TaskType`.
2. Extend `TaskRouter`.
3. Add a tool or assistant branch.
4. Add deterministic validation before execution.

Add a model provider:

1. Implement `LLMClient`.
2. Register it in `build_llm_client`.
3. Keep JSON outputs validated at the caller boundary.
