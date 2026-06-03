# Contributing to DBAide

Thanks for your interest in improving DBAide! This guide covers the dev setup, how to
run the tests, and the conventions we follow.

## Project layout

DBAide is a single Python package with a CLI and a PyQt6 desktop app that share one core
(see the architecture overview in the [README](README.md#architecture) and the full
design in [docs/DESIGN.md](docs/DESIGN.md)).

- `dbaide/agent/` — the agent tool loop, clarifier, SQL writer, controllers, orchestrator.
- `dbaide/desktop/` — the PyQt6 app (`views/`, `components/`, `dialogs/`).
- `dbaide/adapters/` — SQLite / MySQL / PostgreSQL.
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

## Conventions

- **Style:** match the surrounding code — comment density, naming, and idioms. Prefer
  small, focused functions and clear names over cleverness.
- **Safety first:** anything that touches query execution must preserve the read-only,
  single-statement, timeout, and row-limit guarantees. Don't bypass the risk controller.
- **No guessing:** the agent must confirm ambiguous business meaning with the user rather
  than invent defaults — keep that contract intact when editing `agent/`.
- **i18n:** user-facing strings go through `dbaide/i18n.py` (`t(...)`) with both `en` and
  `zh`. Agent answer language follows the UI language.
- **Commits / PRs:** keep commits focused with a clear subject line; describe the user-facing
  effect and the reasoning in the body.

## Reporting issues

Include your OS, Python version, database type, and steps to reproduce. For agent
behavior, the trace (and `dbaide queries <conn> --tail` for executed SQL) is very helpful.
