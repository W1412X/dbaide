# DBAide — Team Operations Guide

This guide covers day-to-day use, troubleshooting, and support workflows for small
teams running DBAide locally (no package signing required).

## Data layout

All local state lives under `~/.dbaide/`:

| Path | Purpose |
|------|---------|
| `config.toml` | Connections, models, UI language/theme, resource limits |
| `logs/dbaide.log` | Rotating application log (stderr also receives logs in CLI) |
| `logs/queries/{connection}.jsonl` | Audit log of executed SQL |
| `assets/instances/{connection}/` | Offline schema/catalog documents |
| `debug/` | Exported debug bundles (ZIP) |

Environment overrides:

- `DBAIDE_CONFIG` — alternate config file path
- `DBAIDE_LOG_DIR` — log directory (default `~/.dbaide/logs`)
- `DBAIDE_LOG_LEVEL` — `DEBUG`, `INFO`, `WARNING`, `ERROR`

## Config upgrades

`config.toml` includes `[meta] config_version`. On startup DBAide migrates older
configs automatically and re-saves them. If you copy configs between machines, ensure
you use the same or newer app version.

Offline asset documents carry their own schema version. If assets were built with a
much older DBAide and the schema tree looks wrong, use **Build Assets** or
**Sync schema** for that connection to rebuild.

## Recommended team setup

1. **Dedicated read-only DB accounts** for production connections (`load_profile = production`).
2. **API keys via environment variables** (`password_env`, `api_key_env`) instead of
   plaintext in `config.toml` when possible.
3. **Set resource limits** in Settings → Resources (row limits, concurrent runs).
4. **Enable debug trace** (Settings) only while investigating agent behaviour.

## Troubleshooting

### Composer stuck on “Asset work in progress”

Usually a background schema/build task did not clear UI state. Wait for the top-bar
badge to return to **Ready**. If it persists:

1. Switch connection away and back, or use **Refresh**.
2. Check `~/.dbaide/logs/dbaide.log` for stuck tasks.
3. Restart the app (closing the window cancels in-flight tasks).

### Model / LLM errors

| Symptom | Action |
|---------|--------|
| “No LLM configured” | Settings → Models → add provider, base URL, model ID, API key |
| Authentication failed | Verify API key and base URL; test with **Test** in settings |
| Rate limit | Wait and retry; reduce concurrent runs |
| Timeout | Increase model timeout; simplify the question |

### Schema empty or stale

- New connection: wait for first-time catalog projection (top bar shows loading state).
- **Sync schema with database** (⋮ menu) after DDL changes in the database.
- **Build Assets** for enriched summaries and better agent accuracy.

### SQL results truncated

Workbench SQL uses row limits from Settings → Resources. The Messages tab explains
when results are truncated. Use `WHERE` / `LIMIT` or browse large tables via the
**Data** tab (paginated).

## Exporting a debug bundle

**Desktop:** ⋮ menu → **Export debug bundle…**

Creates a ZIP under `~/.dbaide/debug/` containing:

- Sanitized `config.json` (passwords/API keys redacted)
- Active session trace (if any)
- Environment metadata
- Tail of `dbaide.log`

**CLI:** `dbaide ask "…" --export-debug` after a workflow run.

Attach the ZIP when reporting issues internally.

## Backups

Back up before major upgrades:

```bash
tar czf dbaide-backup-$(date +%Y%m%d).tar.gz ~/.dbaide/config.toml ~/.dbaide/assets ~/.dbaide/joins
```

Query logs and chat sessions are optional; they can be large.

## CI / quality

The repository runs `pytest` on push and pull requests (headless Qt). Before merging
changes that touch the agent or desktop UI, run locally:

```bash
pip install -e ".[gui,dev]"
pytest -q
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) and [DESIGN.md](DESIGN.md) for architecture
details.
