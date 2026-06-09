# Contributing to DBAide

Thanks for your interest in improving DBAide! This guide covers the dev setup, how to
run the tests, and the conventions we follow.

## Project layout

DBAide is a single Python package with a CLI and a PyQt6 desktop app that share one core
(see the architecture overview in the [README](README.md#architecture) and the full
design in [docs/DESIGN.md](docs/DESIGN.md)). For team operations and troubleshooting,
see [docs/TEAM.md](docs/TEAM.md).

- `dbaide/agent/` — the agent tool loop, clarifier, SQL writer, controllers, orchestrator.
  - `loop.py` — `AskAgentLoop`, the single LLM tool-calling loop.
  - `orchestrator.py` — `AskOrchestrator`, sets up context and runs the loop.
  - `run_state.py` — per-run state (schemas, relations, working memory).
  - `toolkit/` — tool implementations: schema, SQL, profile, catalog, memory, interaction.
- `dbaide/core/` — result types (`WorkflowRequest`, `WorkflowResult`), events, errors.
- `dbaide/db/` — connection pool, resource policy, query budget.
- `dbaide/validation/` — deterministic SQL guards (`SchemaGuard`, CTE parser).
- `dbaide/desktop/` — the PyQt6 app.
  - `views/` — main window, sidebar, topbar, workbench, ask tab, SQL tab.
  - `components/` — composer, conversation view, session list, SQL editor, data table.
  - `dialogs/` — settings, connection, joins, build assets, note editor.
- `dbaide/adapters/` — SQLite / MySQL / PostgreSQL.
- `dbaide/annotations/` — schema annotations (business notes on tables/columns).
- `dbaide/joins/` — join catalog (user-saved + agent-discovered edges, per connection).
- `dbaide/history/` — chat sessions, query history, debug bundles.
- `tests/` — pytest suite (GUI tests render off-screen).

## Setup

Requires **Python 3.11+** (the codebase uses runtime `X | Y` unions).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[gui,dev]"
```

## Running

```bash
dbaide-gui                 # desktop app
dbaide ask "..." --conn local   # CLI
```

## Tests

The full suite must pass before a PR is merged:

```bash
pytest -q
```

GUI/widget tests run headlessly via `QT_QPA_PLATFORM=offscreen` (set automatically by
`tests/conftest.py`), so no display is needed. When changing UI, a quick way to eyeball
the result off-screen is the screenshot harness:

```bash
QT_QPA_PLATFORM=offscreen python tools/shoot.py          # main window → /tmp/shots
QT_QPA_PLATFORM=offscreen python tools/shoot_dialogs.py  # dialogs
```

Please add or update tests for behavior you change.

Note: use the project venv (Python 3.11+), not the system Python. If you use `uv`:

```bash
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[gui,dev]"
```

## Conventions

- **Style:** match the surrounding code — comment density, naming, and idioms. Prefer
  small, focused functions and clear names over cleverness.
- **Safety first:** anything that touches query execution must preserve the read-only,
  single-statement, timeout, and row-limit guarantees. Don't bypass the risk controller.
- **No guessing:** the agent must confirm ambiguous business meaning with the user rather
  than invent defaults — keep that contract intact when editing `agent/`.
- **`__slots__`:** core data classes (`WorkflowRequest`, `WorkflowResult`, etc.) use
  `__slots__`. Add new fields to the `__slots__` tuple before setting them in `__init__`.
- **i18n:** user-facing strings go through `dbaide/i18n.py` (`t(...)`) with both `en` and
  `zh`. Agent answer language follows the UI language.
- **Desktop threading:** all Qt widget access must happen on the main thread. The agent
  loop runs in a worker thread and communicates via signals. Modal menus and dialogs
  block the main thread — avoid launching them from slots that may race.
- **Commits / PRs:** keep commits focused with a clear subject line; describe the user-facing
  effect and the reasoning in the body.

## Reporting issues

Include your OS, Python version, database type, and steps to reproduce. For agent
behavior, the trace (and `dbaide queries <conn> --tail` for executed SQL) is very helpful.
