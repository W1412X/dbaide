from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path

from dbaide.adapters import build_adapter
from dbaide.agent import DataAssistant, InstanceTarget, MultiInstanceAssistant
from dbaide.assets import AssetBuilder, AssetSearch, AssetStore
from dbaide.config import ConfigManager
from dbaide.core.result import WorkflowRequest, WorkflowResult, WorkflowStatus
from dbaide.core.workflow import WorkflowEngine
from dbaide.history.debug_bundle import create_debug_bundle
from dbaide.joins import JoinCatalogStore
from dbaide.llm import NullLLMClient, build_llm_client
from dbaide.models import ConnectionConfig, QueryResult
from dbaide.session import Session
from dbaide.tools import DeveloperTools, ProfileTools, QueryTools, SchemaTools

logger = logging.getLogger("dbaide")

EXIT_WORDS = {"exit", "quit", "\\q"}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", 0), getattr(args, "quiet", False))
    cfg = ConfigManager()
    from dbaide.i18n import set_language
    set_language(cfg.ui_language())
    try:
        return dispatch(args, cfg)
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as exc:
        logger.error("command failed: %s", exc, exc_info=getattr(args, "verbose", 0) > 0)
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _setup_logging(verbosity: int, quiet: bool = False) -> None:
    from dbaide.observability.app_logging import setup_app_logging
    setup_app_logging(verbose=verbosity, quiet=quiet)


def build_parser() -> argparse.ArgumentParser:
    from dbaide import __version__
    parser = argparse.ArgumentParser(prog="dbaide", description="Lightweight CLI data assistant.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity (-v=info, -vv=debug)")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress non-error output")
    sub = parser.add_subparsers(dest="command", required=True)

    connect = sub.add_parser("connect", help="Manage connections")
    csub = connect.add_subparsers(dest="connect_command", required=True)
    add = csub.add_parser("add", help="Add or update a connection")
    add.add_argument("name")
    add.add_argument("--type", required=True, choices=["sqlite", "mysql", "postgres", "postgresql", "mariadb"])
    add.add_argument("--database", default="")
    add.add_argument("--path", default="")
    add.add_argument("--host", default="")
    add.add_argument("--port", type=lambda v: _bounded_int(v, min_val=1, max_val=65535, name="port"))
    add.add_argument("--user", default="")
    add.add_argument("--password-env", default="")
    add.add_argument("--password", default="")
    add.add_argument("--session-timezone", default="UTC", help="Session time zone set after connecting. Default: UTC.")
    add.add_argument("--sslmode", default="", choices=["", "disable", "allow", "prefer", "require", "verify-ca", "verify-full"],
                     help="TLS mode for remote (postgres/mysql) connections. verify-ca/verify-full validate the server certificate. Default: driver default.")
    add.add_argument("--ssl-ca", default="", help="Path to a CA certificate bundle for verify-ca/verify-full (default: system/certifi trust store).")
    add.add_argument("--table-allow", action="append", default=[], help="Restrict the agent to ONLY these tables (repeatable). Default: all tables allowed.")
    add.add_argument("--table-deny", action="append", default=[], help="Forbid the agent from referencing these tables (repeatable).")
    add.add_argument("--default", action="store_true")
    add.add_argument("--skip-assets", action="store_true", help="Save connection without building offline schema assets.")
    add.add_argument("--asset-database", action="append", default=[], help="Database/schema to initialize. Repeatable. Default: all visible databases.")
    add.add_argument("--profile-mode", choices=["none", "light", "auto", "all"], default=None, help="Column profiling policy. Default: from load profile (production=light).")
    add.add_argument("--no-profile", action="store_true", help="Skip column profiling during asset initialization.")
    add.add_argument("--no-sample", action="store_true", help="Skip table sampling during asset initialization.")
    add.add_argument("--top-k", type=int, default=30, help="Top distinct values to keep per profiled column.")
    add.add_argument("--sample-limit", type=int, default=50, help="Sample values/rows to keep per table or column.")
    add.add_argument("--timeout", type=lambda v: _bounded_int(v, min_val=0, max_val=7200, name="timeout"), default=0, help="Overall time budget in seconds for asset build. 0 = unlimited.")
    add.add_argument("--load-profile", choices=["production", "staging", "dev"], default="production", help="Resource safety profile for this connection. Default: production (lowest DB load).")
    add.add_argument("--max-workers", type=lambda v: _bounded_int(v, min_val=1, max_val=32, name="max-workers"), default=None, help="Build concurrency. Default: from load profile.")
    add.add_argument("--dry-run", action="store_true", help="Estimate the query count without touching table data.")
    csub.add_parser("list", help="List configured connections")
    test = csub.add_parser("test", help="Test a connection")
    test.add_argument("name", nargs="?")

    ask = sub.add_parser("ask", help="Ask a database question")
    add_common_conn_args(ask)
    ask.add_argument("question")
    ask.add_argument("--debug-trace", action="store_true",
                     help="Full debug trace: every step's args/output + each LLM call's "
                          "prompt and response (set DBAIDE_TRACE_LLM=1 to capture LLM I/O).")
    ask.add_argument("--export-debug", action="store_true")
    ask.add_argument("--json", action="store_true")

    chat = sub.add_parser("chat", help="Interactive assistant")
    add_common_conn_args(chat)

    find = sub.add_parser("find", help="Find tables/columns from offline assets")
    find.add_argument("query")
    find.add_argument("--conn", default="", help="Connection name, comma-separated names, or `all`")
    find.add_argument("--limit", type=int, default=12)
    find.add_argument("--json", action="store_true")

    tree = sub.add_parser("tree", help="Print schema tree from offline assets")
    tree.add_argument("--conn", default="", help="Connection name")
    tree.add_argument("--database", default="")
    tree.add_argument("--no-columns", action="store_true")
    tree.add_argument("--max-columns", type=int, default=80)

    ddl = sub.add_parser("ddl", help="Show table DDL")
    ddl.add_argument("table")
    ddl.add_argument("--conn", default="")
    ddl.add_argument("--database", default="")

    relations = sub.add_parser("relations", help="Show FK and join hints from assets")
    relations.add_argument("--conn", default="")
    relations.add_argument("--database", default="")
    relations.add_argument("--json", action="store_true")

    annotate = sub.add_parser(
        "annotate",
        help="Add/list/remove authoritative user notes on a database/table/column",
    )
    ansub = annotate.add_subparsers(dest="annotate_command", required=True)
    an_add = ansub.add_parser("add", help="Add or update a note (upserts by object)")
    an_add.add_argument("note", help="The note text, e.g. 'UTC timestamp; show +8' or 'deprecated, use orders_v2'")
    an_add.add_argument("--conn", default="", help="Connection name")
    an_add.add_argument("--database", default="", help="Database/schema (optional; omit for a connection-wide note)")
    an_add.add_argument("--table", default="", help="Table name (omit for a database-level note)")
    an_add.add_argument("--column", default="", help="Column name (requires --table)")
    an_list = ansub.add_parser("list", help="List notes for a connection")
    an_list.add_argument("--conn", default="")
    an_list.add_argument("--database", default="")
    an_list.add_argument("--table", default="")
    an_list.add_argument("--json", action="store_true")
    an_rm = ansub.add_parser("rm", help="Remove a note by id, or by object (scope/database/table/column)")
    an_rm.add_argument("--conn", default="")
    an_rm.add_argument("--id", default="", help="Annotation id (from `annotate list`)")
    an_rm.add_argument("--database", default="")
    an_rm.add_argument("--table", default="")
    an_rm.add_argument("--column", default="")

    doc = sub.add_parser("doc", help="Export schema markdown from assets")
    doc.add_argument("--conn", default="")
    doc.add_argument("--database", default="")
    doc.add_argument("--out", default="")

    diff = sub.add_parser("diff", help="Diff two asset schemas")
    diff.add_argument("left", help="Path like dev or dev.shop")
    diff.add_argument("right", help="Path like prod or prod.shop")
    diff.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect", help="Inspect a table")
    add_common_conn_args(inspect)
    inspect.add_argument("table")

    profile = sub.add_parser("profile", help="Profile a table or selected columns")
    add_common_conn_args(profile)
    profile.add_argument("table")
    profile.add_argument("--columns", default="", help="Comma-separated columns")

    sql = sub.add_parser("sql", help="Validate and optionally execute SQL")
    add_common_conn_args(sql)
    sql.add_argument("sql")
    sql.add_argument("--execute", action="store_true")
    sql.add_argument("--out", default="")

    diagnose = sub.add_parser("diagnose", help="Diagnose SQL with validation and EXPLAIN")
    add_common_conn_args(diagnose)
    diagnose.add_argument("sql")

    assets = sub.add_parser("assets", help="Build or inspect offline schema assets")
    asub = assets.add_subparsers(dest="assets_command", required=True)
    build = asub.add_parser("build", help="Build offline assets for a connection")
    build.add_argument("conn")
    build.add_argument("--database", action="append", default=[], help="Database/schema to initialize. Repeatable. Default: all visible databases.")
    build.add_argument("--profile-mode", choices=["none", "light", "auto", "all"], default=None, help="Default: from load profile (production=light).")
    build.add_argument("--no-profile", action="store_true")
    build.add_argument("--no-sample", action="store_true")
    build.add_argument("--top-k", type=int, default=30)
    build.add_argument("--sample-limit", type=int, default=50)
    build.add_argument("--timeout", type=lambda v: _bounded_int(v, min_val=0, max_val=7200, name="timeout"), default=0, help="Overall time budget in seconds. 0 = unlimited.")
    build.add_argument("--per-column-timeout", type=lambda v: _bounded_int(v, min_val=1, max_val=300, name="per-column-timeout"), default=30, help="Timeout per column profile in seconds.")
    build.add_argument("--load-profile", choices=["production", "staging", "dev"], default=None, help="Override the connection's resource profile for this build.")
    build.add_argument("--max-workers", type=lambda v: _bounded_int(v, min_val=1, max_val=32, name="max-workers"), default=None, help="Build concurrency. Default: from load profile.")
    build.add_argument("--dry-run", action="store_true", help="Estimate the query count without touching table data.")
    status = asub.add_parser("status", help="Show asset status")
    status.add_argument("conn", nargs="?")
    show = asub.add_parser("show", help="Show an asset document")
    show.add_argument("path", help="Path like instance, instance.database, instance.database.table, or instance.database.table.column")
    enrich = asub.add_parser("enrich", help="Profile selected table/columns and update offline assets")
    enrich.add_argument("conn")
    enrich.add_argument("--database", required=True)
    enrich.add_argument("--table", required=True)
    enrich.add_argument("--columns", default="", help="Comma-separated columns. Default: all columns in table.")
    enrich.add_argument("--top-k", type=int, default=50)
    enrich.add_argument("--sample-limit", type=int, default=80)

    queries = sub.add_parser("queries", help="Show the SQL query audit log for a connection")
    queries.add_argument("conn", nargs="?", help="Connection name. Default: the default connection.")
    queries.add_argument("--tail", type=lambda v: _bounded_int(v, min_val=1, max_val=10000, name="tail"), default=50, help="Number of recent queries to show.")
    queries.add_argument("--json", action="store_true")

    # ── model management ────────────────────────────────────────────────────
    model = sub.add_parser("model", help="Manage LLM model configurations")
    msub = model.add_subparsers(dest="model_command", required=True)
    msub.add_parser("list", help="List configured models")
    m_add = msub.add_parser("add", help="Add or update a model config")
    m_add.add_argument("name")
    m_add.add_argument("--provider", default="openai_compatible", choices=["openai_compatible", "none"])
    m_add.add_argument("--base-url", default="", help="API base URL")
    m_add.add_argument("--api-key-env", default="", help="Env var containing the API key")
    m_add.add_argument("--api-key", default="", help="API key (prefer --api-key-env)")
    m_add.add_argument("--model", default="", help="Model name/ID")
    m_add.add_argument("--timeout", type=lambda v: _bounded_int(v, min_val=1, max_val=600, name="timeout"), default=60, help="Request timeout in seconds (1-600)")
    m_add.add_argument("--context-length", default="32k", help="Context window size (e.g. 32k, 128k, 1m)")
    m_add.add_argument("--default", action="store_true", help="Set as the default model")
    m_del = msub.add_parser("delete", help="Delete a model config")
    m_del.add_argument("name")
    m_setdef = msub.add_parser("set-default", help="Set the default model")
    m_setdef.add_argument("name")
    m_test = msub.add_parser("test", help="Test an LLM model")
    m_test.add_argument("name", nargs="?", help="Model name (default: the default model)")

    # ── config (resource defaults) ──────────────────────────────────────────
    config = sub.add_parser("config", help="View or modify resource defaults")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("show", help="Show current resource defaults and agent parameters")
    cfg_set = config_sub.add_parser("set", help="Set a resource default")
    cfg_set.add_argument("key", help="Parameter name (e.g. max_workers, query_timeout, max_concurrent_runs)")
    cfg_set.add_argument("value", help="Parameter value")
    config_sub.add_parser("reset", help="Reset all resource defaults to built-in values")

    # ── session management ──────────────────────────────────────────────────
    sess = sub.add_parser("session", help="Manage chat sessions")
    sess_sub = sess.add_subparsers(dest="session_command", required=True)
    sess_list = sess_sub.add_parser("list", help="List saved sessions")
    sess_list.add_argument("--conn", default="")
    sess_list.add_argument("--limit", type=int, default=20)
    sess_show = sess_sub.add_parser("show", help="Show a session's conversation")
    sess_show.add_argument("session_id")
    sess_show.add_argument("--conn", default="")
    sess_show.add_argument("--json", action="store_true")
    sess_rename = sess_sub.add_parser("rename", help="Rename a session")
    sess_rename.add_argument("session_id")
    sess_rename.add_argument("title")
    sess_rename.add_argument("--conn", default="")
    sess_del = sess_sub.add_parser("delete", help="Delete a session")
    sess_del.add_argument("session_id")
    sess_del.add_argument("--conn", default="")

    # ── join catalog ────────────────────────────────────────────────────────
    join = sub.add_parser("join", help="Manage join hints")
    jsub = join.add_subparsers(dest="join_command", required=True)
    j_list = jsub.add_parser("list", help="List join relationships")
    j_list.add_argument("--conn", default="")
    j_list.add_argument("--database", default="")
    j_list.add_argument("--json", action="store_true")
    j_add = jsub.add_parser("add", help="Add a join hint")
    j_add.add_argument("--conn", default="")
    j_add.add_argument("--database", default="")
    j_add.add_argument("--table", required=True, help="Left table")
    j_add.add_argument("--column", required=True, help="Left column")
    j_add.add_argument("--ref-table", required=True, help="Right table")
    j_add.add_argument("--ref-column", required=True, help="Right column")
    j_del = jsub.add_parser("delete", help="Delete a join hint")
    j_del.add_argument("--conn", default="")
    j_del.add_argument("--id", default="", help="Join id")
    j_del.add_argument("--table", default="")
    j_del.add_argument("--column", default="")
    j_del.add_argument("--ref-table", default="")
    j_del.add_argument("--ref-column", default="")

    # ── history ─────────────────────────────────────────────────────────────
    hist = sub.add_parser("history", help="Manage workflow run history")
    hsub = hist.add_subparsers(dest="history_command", required=True)
    h_list = hsub.add_parser("list", help="List recent workflow runs")
    h_list.add_argument("--conn", default="")
    h_list.add_argument("--limit", type=int, default=20)
    h_list.add_argument("--json", action="store_true")
    h_del = hsub.add_parser("delete", help="Delete a history entry")
    h_del.add_argument("workflow_id")
    h_del.add_argument("--conn", default="")

    # ── export / import ─────────────────────────────────────────────────────
    exp = sub.add_parser("export", help="Export connections, models and config")
    exp.add_argument("--conn", default="", help="Export a single connection (config + joins + notes)")
    exp.add_argument("--all", action="store_true", dest="export_all", help="Export everything")
    exp.add_argument("--out", default="", help="Output file path (default: stdout)")

    imp = sub.add_parser("import", help="Import from a DBAide export file")
    imp.add_argument("file", help="Path to the export JSON file")

    # ── Backup ──────────────────────────────────────────────────────────────
    bk = sub.add_parser("backup", help="Backup tables, databases, or instances to local files")
    bsub = bk.add_subparsers(dest="backup_command", required=True)

    bk_table = bsub.add_parser("table", help="Backup a single table")
    bk_table.add_argument("conn", help="Connection name")
    bk_table.add_argument("database", help="Database name")
    bk_table.add_argument("table", help="Table name")
    bk_table.add_argument("--format", dest="fmt", choices=["csv", "sql", "sqlite"], default="csv")
    bk_table.add_argument("--batch-size", type=int, default=5000)

    bk_db = bsub.add_parser("db", help="Backup all tables in a database")
    bk_db.add_argument("conn", help="Connection name")
    bk_db.add_argument("database", help="Database name")
    bk_db.add_argument("--format", dest="fmt", choices=["csv", "sql", "sqlite"], default="csv")
    bk_db.add_argument("--batch-size", type=int, default=5000)
    bk_db.add_argument("--threads", type=int, default=4)

    bk_inst = bsub.add_parser("instance", help="Backup all databases in a connection")
    bk_inst.add_argument("conn", help="Connection name")
    bk_inst.add_argument("--format", dest="fmt", choices=["csv", "sql", "sqlite"], default="csv")
    bk_inst.add_argument("--batch-size", type=int, default=5000)
    bk_inst.add_argument("--threads", type=int, default=4)

    bk_list = bsub.add_parser("list", help="List backup history")
    bk_list.add_argument("--conn", default="")
    bk_list.add_argument("--database", default="")
    bk_list.add_argument("--table", default="")

    bk_del = bsub.add_parser("delete", help="Delete a backup by ID")
    bk_del.add_argument("id", type=int, help="Backup ID")

    # ── MCP server & setup (AI agent integration) ────────────────────────────
    mcp = sub.add_parser("mcp", help="Start the MCP (Model Context Protocol) server on stdio")
    mcp.add_argument("--mode", choices=["full", "ask", "tools"], default="full",
                     help="full = ask + atomic tools (default), ask = AI pipeline only, tools = atomic tools only")

    from dbaide.skill import SUPPORTED_TOOLS as _SUPPORTED_TOOLS
    setup = sub.add_parser("setup", help="Register dbaide as an MCP server in a coding tool's config")
    setup.add_argument("tool", nargs="?", default="",
                       help=f"Tool name ({', '.join(_SUPPORTED_TOOLS)}), or omit for --all")
    setup.add_argument("--mode", choices=["full", "ask", "tools"], default="full",
                       help="full = ask + atomic tools (default), "
                            "ask = AI pipeline only, tools = atomic tools only")
    setup.add_argument("--all", action="store_true", dest="setup_all",
                       help="Register in ALL supported tools at once")
    setup.add_argument("--uninstall", action="store_true",
                       help="Remove the dbaide MCP server entry instead of adding it")

    return parser


def _bounded_int(value: str, *, min_val: int = 1, max_val: int = 10_000_000, name: str = "value") -> int:
    try:
        n = int(value)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer, got {value!r}") from exc
    if not (min_val <= n <= max_val):
        raise argparse.ArgumentTypeError(f"{name} must be {min_val}-{max_val}, got {n}")
    return n


def add_common_conn_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--conn", default="", help="Connection name, comma-separated names, or `all`")
    parser.add_argument("--database", default="", help="Database/schema override. For multiple connections use conn=db pairs: local=main,prod=app")
    parser.add_argument("--limit", type=lambda v: _bounded_int(v, min_val=1, max_val=1_000_000, name="limit"), default=100)
    parser.add_argument("--timeout", type=lambda v: _bounded_int(v, min_val=1, max_val=3600, name="timeout"), default=60)


def dispatch(args: argparse.Namespace, cfg: ConfigManager) -> int:
    if args.command == "connect":
        return dispatch_connect(args, cfg)
    if args.command == "assets":
        return dispatch_assets(args, cfg)
    if args.command == "ask":
        result = run_workflow_cli(cfg, args)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
        else:
            print(result.answer_markdown or result.answer_plaintext)
            if args.debug_trace:
                from dbaide.agent.trace_model import render_events_text
                events = [e.metadata for e in result.trace if getattr(e, "metadata", None)]
                print("\n" + "=" * 60 + "\nDEBUG TRACE\n" + "=" * 60)
                print(render_events_text(events))
                if not os.environ.get("DBAIDE_TRACE_LLM"):
                    print("\n(LLM prompts/responses not captured — rerun with "
                          "DBAIDE_TRACE_LLM=1 to include them.)")
            if args.export_debug:
                print(f"\nDebug bundle: {create_debug_bundle(result)}")
        return 0
    if args.command == "find":
        return dispatch_find(args, cfg)
    if args.command == "tree":
        conn = cfg.get_connection(args.conn or None)
        print(DeveloperTools().tree(conn.name, database=args.database, show_columns=not args.no_columns, max_columns=args.max_columns))
        return 0
    if args.command == "ddl":
        conn = cfg.get_connection(args.conn or None)
        adapter = build_adapter(conn)
        print(adapter.get_table_ddl(args.table, database=args.database))
        return 0
    if args.command == "relations":
        conn = cfg.get_connection(args.conn or None)
        rows = DeveloperTools().relations(conn.name, database=args.database)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        else:
            if not rows:
                print("No relations found.")
            for row in rows:
                print(f"{row['database']}.{row['table']}.{row['column']} -> {row['ref_table']}.{row['ref_column']} ({row['source']})")
        return 0
    if args.command == "annotate":
        return dispatch_annotate(args, cfg)
    if args.command == "doc":
        conn = cfg.get_connection(args.conn or None)
        text = DeveloperTools().markdown(conn.name, database=args.database)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0
    if args.command == "diff":
        left_instance, left_db = _parse_asset_root(args.left)
        right_instance, right_db = _parse_asset_root(args.right)
        result = DeveloperTools().diff(left_instance, right_instance, left_database=left_db, right_database=right_db)
        payload = {
            "missing_tables_left": result.missing_tables_left,
            "missing_tables_right": result.missing_tables_right,
            "column_diffs": result.column_diffs,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            print_diff(payload)
        return 0
    if args.command == "chat":
        assistant = build_any_assistant(cfg, args)
        print("DBAide chat started. Type exit/quit/\\q to end.")
        while True:
            try:
                question = input("db> ").strip()
            except EOFError:
                print()
                return 0
            if not question:
                continue
            if question.lower() in EXIT_WORDS:
                return 0
            try:
                _chat_turn(assistant, question, database=args.database)
            except KeyboardInterrupt:
                print("\n(interrupted)")
            except Exception as exc:  # noqa: BLE001 — one bad turn must not kill the session
                print(f"error: {exc}")
    if args.command == "inspect":
        adapter, session = build_adapter_session(cfg, args)
        schema = SchemaTools(adapter, session.disclosure)
        info = schema.inspect_table(args.table, database=args.database)
        from dbaide.agent.assistant import format_inspect
        print(format_inspect(info))
        return 0
    if args.command == "profile":
        adapter, session = build_adapter_session(cfg, args)
        profile_tools = ProfileTools(adapter, session.disclosure)
        columns = [c.strip() for c in args.columns.split(",") if c.strip()] or None
        profiles = profile_tools.profile_table(args.table, columns, database=args.database)
        if not profiles:
            print("No profiles generated.")
            return 0
        for prof in profiles:
            null_pct = f"{(prof.null_count / prof.row_count * 100):.1f}%" if prof.row_count else "N/A"
            print(f"\n{prof.table}.{prof.column}")
            print(f"  Type: {prof.data_kind or 'unknown'}")
            print(f"  Rows: {prof.row_count:,}  |  Null: {prof.null_count:,} ({null_pct})  |  Distinct: {prof.distinct_count or 'N/A'}")
            if prof.min_value is not None or prof.max_value is not None:
                print(f"  Range: {prof.min_value} .. {prof.max_value}")
            if prof.top_values:
                top_str = ", ".join(f"{x.get('value')}({x.get('count')})" for x in prof.top_values[:5])
                print(f"  Top: {top_str}")
        return 0
    if args.command == "sql":
        adapter, session = build_adapter_session(cfg, args)
        query = QueryTools(adapter, session.disclosure, default_limit=args.limit, timeout_seconds=args.timeout)
        validation = query.validate_sql(args.sql, add_limit=True)
        if not validation.ok:
            for issue in validation.issues:
                print(f"[{issue.code}] {issue.message}", file=sys.stderr)
            return 2
        if not args.execute:
            print(validation.normalized_sql)
            return 0
        result = query.execute_sql(validation.normalized_sql, database=args.database, limit=args.limit)
        if args.out:
            write_result(result, Path(args.out))
        else:
            print_result(result)
        return 0
    if args.command == "diagnose":
        adapter, session = build_adapter_session(cfg, args)
        query = QueryTools(adapter, session.disclosure, default_limit=args.limit, timeout_seconds=args.timeout)
        from dbaide.tools import DiagnoseTools

        report = DiagnoseTools(query).diagnose_sql(args.sql, database=args.database)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "queries":
        return dispatch_queries(args, cfg)
    if args.command == "model":
        return dispatch_model(args, cfg)
    if args.command == "config":
        return dispatch_config(args, cfg)
    if args.command == "session":
        return dispatch_session(args, cfg)
    if args.command == "join":
        return dispatch_join(args, cfg)
    if args.command == "history":
        return dispatch_history(args, cfg)
    if args.command == "export":
        return dispatch_export(args, cfg)
    if args.command == "import":
        return dispatch_import(args, cfg)
    if args.command == "backup":
        return dispatch_backup(args, cfg)
    if args.command == "mcp":
        return dispatch_mcp(args, cfg)
    if args.command == "setup":
        return dispatch_setup(args, cfg)
    raise AssertionError(args.command)


def dispatch_model(args: argparse.Namespace, cfg: ConfigManager) -> int:
    from dbaide.models import ModelConfig

    if args.model_command == "list":
        models = cfg.models()
        default = str(cfg._data.get("default_model") or "")
        if not models:
            print("No models configured. Run `dbaide model add <name> --provider openai_compatible --base-url <url> --model <model>` to add one.")
            return 0
        print(f"{'Name':<20} {'Provider':<18} {'Model':<24} {'Context':<10} {'Default'}")
        print("-" * 80)
        for name, m in models.items():
            is_default = " *" if name == default else ""
            ctx = f"{m.context_length // 1000}k" if m.context_length >= 1000 else str(m.context_length)
            print(f"{name:<20} {m.provider:<18} {m.model or '(not set)':<24} {ctx:<10} {is_default}")
        return 0

    if args.model_command == "add":
        m = ModelConfig(
            name=args.name,
            provider=args.provider,
            base_url=getattr(args, "base_url", ""),
            api_key_env=getattr(args, "api_key_env", ""),
            api_key=getattr(args, "api_key", ""),
            model=args.model,
            timeout_seconds=args.timeout,
            context_length=getattr(args, "context_length", "32k"),
        )
        cfg.upsert_model(m, make_default=args.default)
        ctx = f"{m.context_length // 1000}k" if m.context_length >= 1000 else str(m.context_length)
        print(f"saved model: {args.name} (provider={m.provider}, model={m.model}, context={ctx})")
        return 0

    if args.model_command == "delete":
        cfg.delete_model(args.name)
        print(f"deleted model: {args.name}")
        return 0

    if args.model_command == "set-default":
        cfg.set_default_model(args.name)
        print(f"default model: {args.name}")
        return 0

    if args.model_command == "test":
        m = cfg.model(args.name if args.name else None)
        if m.provider == "none":
            print(f"model '{m.name}' has provider=none — nothing to test.", file=sys.stderr)
            return 1
        from dbaide.llm import LLMMessage
        llm = build_llm_client(m)
        result = llm.complete_text([LLMMessage("user", "Say 'hello' in one word.")])
        print(f"ok: {m.name} (provider={m.provider}, model={m.model})")
        print(f"response: {str(result).strip()[:200]}")
        return 0

    raise AssertionError(args.model_command)


def dispatch_config(args: argparse.Namespace, cfg: ConfigManager) -> int:
    if args.config_command == "show":
        from dbaide.db.policy import LOAD_PROFILES
        from dataclasses import asdict, fields
        defaults = cfg.resource_defaults()
        print("Resource defaults (user overrides):")
        if not defaults:
            print("  (none — using built-in defaults)")
        else:
            for k, v in sorted(defaults.items()):
                print(f"  {k} = {v}")
        print()
        print("Built-in presets:")
        for name, profile in LOAD_PROFILES.items():
            vals = asdict(profile)
            summary = ", ".join(f"{k}={v}" for k, v in vals.items())
            print(f"  {name}: {summary}")
        return 0

    if args.config_command == "set":
        defaults = cfg.resource_defaults()
        key = args.key
        try:
            value: int | float | str = int(args.value)
        except ValueError:
            try:
                value = float(args.value)
            except ValueError:
                value = args.value
        if key == "compress_threshold" and isinstance(value, float) and value < 1.0:
            print(f"hint: compress_threshold is an integer percentage (50–95), not a ratio."
                  f" Did you mean {int(value * 100)}?", file=sys.stderr)
            return 1
        defaults[key] = value
        cfg.set_resource_defaults(defaults)
        print(f"set {key} = {value}")
        return 0

    if args.config_command == "reset":
        cfg.set_resource_defaults({})
        print("resource defaults reset to built-in values")
        return 0

    raise AssertionError(args.config_command)


def dispatch_session(args: argparse.Namespace, cfg: ConfigManager) -> int:
    from dbaide.history.session_store import ChatSessionStore

    store = ChatSessionStore()
    conn_name = args.conn or cfg.get_connection(None).name

    if args.session_command == "list":
        sessions = store.list_sessions(conn_name, limit=args.limit)
        if not sessions:
            print(f"No sessions for {conn_name}.")
            return 0
        import datetime
        print(f"{'ID':<14} {'Turns':>5}  {'Updated':<20} Title")
        print("-" * 75)
        for s in sessions:
            ts = datetime.datetime.fromtimestamp(s.get("updated_at") or 0).strftime("%Y-%m-%d %H:%M")
            print(f"{s['session_id']:<14} {s.get('turn_count', 0):>5}  {ts:<20} {s.get('title', '')}")
        return 0

    if args.session_command == "show":
        data = store.load(conn_name, args.session_id)
        if data is None:
            print(f"Session not found: {conn_name}/{args.session_id}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
            return 0
        print(f"Session: {data.get('title', '')} ({args.session_id})")
        print("-" * 60)
        for i, turn in enumerate(data.get("turns") or [], 1):
            q = turn.get("question", "")
            a = turn.get("answer_markdown", "")
            print(f"\n[{i}] Q: {q}")
            if a:
                print(f"    A: {a[:500]}{'...' if len(a) > 500 else ''}")
        return 0

    if args.session_command == "rename":
        ok = store.rename(conn_name, args.session_id, args.title)
        print("renamed." if ok else "session not found.")
        return 0 if ok else 1

    if args.session_command == "delete":
        ok = store.delete(conn_name, args.session_id)
        print("deleted." if ok else "session not found.")
        return 0 if ok else 1

    raise AssertionError(args.session_command)


def dispatch_join(args: argparse.Namespace, cfg: ConfigManager) -> int:
    from dbaide.joins import JoinCatalogStore
    from dbaide.connection_identity import connection_fingerprint

    catalog = JoinCatalogStore()
    conn = cfg.get_connection(args.conn or None)
    fp = connection_fingerprint(conn)

    if args.join_command == "list":
        joins = catalog.list_records(conn.name, database=args.database, fingerprint=fp)
        if args.json:
            print(json.dumps(joins, ensure_ascii=False, indent=2, default=str))
            return 0
        if not joins:
            print(f"No join hints for {conn.name}.")
            return 0
        for j in joins:
            db = j.get("database", "")
            src = j.get("source", "")
            prefix = f"{db}." if db else ""
            print(f"{prefix}{j.get('table','')}.{j.get('column','')} -> "
                  f"{prefix}{j.get('ref_table','')}.{j.get('ref_column','')}  "
                  f"[{src}] (id: {j.get('id','')})")
        return 0

    if args.join_command == "add":
        record = catalog.add(
            conn.name,
            {
                "table": args.table, "column": args.column,
                "ref_table": args.ref_table, "ref_column": args.ref_column,
            },
            source="user",
            database=args.database or conn.database,
            fingerprint=fp,
        )
        print(f"added join: {record.get('table')}.{record.get('column')} -> "
              f"{record.get('ref_table')}.{record.get('ref_column')} (id: {record.get('id')})")
        return 0

    if args.join_command == "delete":
        endpoint = None
        if args.table and args.column and args.ref_table and args.ref_column:
            endpoint = {
                "table": args.table, "column": args.column,
                "ref_table": args.ref_table, "ref_column": args.ref_column,
            }
        ok = catalog.delete(conn.name, join_id=args.id, endpoint=endpoint, fingerprint=fp)
        print("deleted." if ok else "join not found.")
        return 0 if ok else 1

    raise AssertionError(args.join_command)


def dispatch_history(args: argparse.Namespace, cfg: ConfigManager) -> int:
    from dbaide.history.store import WorkflowHistoryStore

    store = WorkflowHistoryStore()
    conn_name = args.conn or cfg.get_connection(None).name

    if args.history_command == "list":
        entries = store.list_workflows(conn_name, limit=args.limit)
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2, default=str))
            return 0
        if not entries:
            print(f"No history for {conn_name}.")
            return 0
        import datetime
        print(f"{'ID':<14} {'Status':<12} {'Time':<20} Question")
        print("-" * 80)
        for e in entries:
            ts = datetime.datetime.fromtimestamp(e.get("created_at") or 0).strftime("%Y-%m-%d %H:%M")
            q = str(e.get("question") or "")[:50]
            print(f"{e.get('workflow_id',''):<14} {e.get('status',''):<12} {ts:<20} {q}")
        return 0

    if args.history_command == "delete":
        ok = store.delete(conn_name, args.workflow_id)
        print("deleted." if ok else "entry not found.")
        return 0 if ok else 1

    raise AssertionError(args.history_command)


def dispatch_export(args: argparse.Namespace, cfg: ConfigManager) -> int:
    from dbaide.assets import AssetStore
    from dbaide.joins import JoinCatalogStore
    from dbaide.annotations import AnnotationStore
    from datetime import datetime, timezone

    if args.export_all:
        joins_store = JoinCatalogStore()
        ann_store = AnnotationStore()
        connections: list[dict] = []
        all_joins: dict[str, list] = {}
        all_anns: dict[str, list] = {}
        for name, conn in cfg.connections().items():
            d = {"name": conn.name, "type": conn.type, "database": conn.database,
                 "host": conn.host, "port": conn.port, "user": conn.user,
                 "password_env": conn.password_env, "password": conn.password,
                 "path": conn.path,
                 "load_profile": conn.load_profile, "session_timezone": conn.session_timezone}
            connections.append({k: v for k, v in d.items() if v not in (None, "", 0)})
            j = joins_store._load(name)
            if j:
                all_joins[name] = j
            a = ann_store._load(name)
            if a:
                all_anns[name] = a
        models = []
        for name, m in cfg.models().items():
            md = {"name": m.name, "provider": m.provider, "base_url": m.base_url,
                  "api_key_env": m.api_key_env, "api_key": m.api_key,
                  "model": m.model, "timeout_seconds": m.timeout_seconds,
                  "context_length": m.context_length}
            models.append({k: v for k, v in md.items() if v not in (None, "", 0)})
        payload = {
            "dbaide_export": {"version": 1, "type": "full",
                              "exported_at": datetime.now(timezone.utc).isoformat()},
            "connections": connections, "models": models,
            "resource_defaults": cfg.resource_defaults(),
            "joins": all_joins, "annotations": all_anns,
        }
    elif args.conn:
        conn = cfg.get_connection(args.conn)
        joins_store = JoinCatalogStore()
        ann_store = AnnotationStore()
        d = {"name": conn.name, "type": conn.type, "database": conn.database,
             "host": conn.host, "port": conn.port, "user": conn.user,
             "password_env": conn.password_env, "password": conn.password,
             "path": conn.path,
             "load_profile": conn.load_profile, "session_timezone": conn.session_timezone}
        payload = {
            "dbaide_export": {"version": 1, "type": "connection",
                              "exported_at": datetime.now(timezone.utc).isoformat()},
            "connection": {k: v for k, v in d.items() if v not in (None, "", 0)},
            "joins": joins_store._load(conn.name),
            "annotations": ann_store._load(conn.name),
        }
    else:
        print("specify --conn <name> or --all", file=sys.stderr)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"exported to {args.out}")
    else:
        print(text)
    return 0


def dispatch_import(args: argparse.Namespace, cfg: ConfigManager) -> int:
    from dbaide.config import _CONNECTION_KEYS, _MODEL_KEYS
    from dbaide.annotations import AnnotationStore
    from dbaide.joins import JoinCatalogStore

    path = Path(args.file)
    if not path.exists():
        print(f"file not found: {args.file}", file=sys.stderr)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("dbaide_export") or {}
    if meta.get("type") not in ("connection", "full"):
        print("not a valid DBAide export file", file=sys.stderr)
        return 1

    ann_store = AnnotationStore()
    joins_store = JoinCatalogStore()

    if meta["type"] == "full":
        for cd in data.get("connections") or []:
            name = cd.get("name", "")
            if not name:
                continue
            payload = {k: v for k, v in cd.items() if k in _CONNECTION_KEYS}
            payload.setdefault("name", name)
            payload.setdefault("type", "")
            cfg.upsert_connection(ConnectionConfig(**payload))
            print(f"  imported connection: {name}")
        for md in data.get("models") or []:
            name = md.get("name", "")
            if not name:
                continue
            from dbaide.models import ModelConfig
            payload = {k: v for k, v in md.items() if k in _MODEL_KEYS}
            payload.setdefault("name", name)
            cfg.upsert_model(ModelConfig(**payload))
            print(f"  imported model: {name}")
        if data.get("resource_defaults"):
            cfg.set_resource_defaults(data["resource_defaults"])
            print("  imported resource defaults")
        for conn_name, joins in (data.get("joins") or {}).items():
            if joins:
                joins_store._save(conn_name, joins)
                print(f"  imported {len(joins)} joins for {conn_name}")
        for conn_name, anns in (data.get("annotations") or {}).items():
            for ann in anns:
                ann_store.add(conn_name, scope=ann.get("scope", "table"),
                              database=ann.get("database", ""),
                              table=ann.get("table", ""),
                              column=ann.get("column", ""),
                              note=ann.get("note", ""),
                              source=ann.get("source", "user"))
            if anns:
                print(f"  imported {len(anns)} annotations for {conn_name}")
    else:
        cd = data.get("connection") or {}
        name = cd.get("name", "")
        if not name:
            print("export file is missing a connection name", file=sys.stderr)
            return 1
        payload = {k: v for k, v in cd.items() if k in _CONNECTION_KEYS}
        payload.setdefault("name", name)
        payload.setdefault("type", "")
        cfg.upsert_connection(ConnectionConfig(**payload))
        print(f"imported connection: {name}")
        joins = data.get("joins") or []
        if joins:
            joins_store._save(name, joins)
            print(f"  {len(joins)} joins")
        anns = data.get("annotations") or []
        for ann in anns:
            ann_store.add(name, scope=ann.get("scope", "table"),
                          database=ann.get("database", ""),
                          table=ann.get("table", ""),
                          column=ann.get("column", ""),
                          note=ann.get("note", ""),
                          source=ann.get("source", "user"))
        if anns:
            print(f"  {len(anns)} annotations")

    print("import complete.")
    return 0


def dispatch_backup(args: argparse.Namespace, cfg: ConfigManager) -> int:
    from dbaide.backup import BackupEngine, BackupRegistry

    def _progress(table: str, done: int, total: int | None) -> None:
        if total:
            pct = min(done / total * 100, 100)
            print(f"\r  {table}: {done:,}/{total:,} rows ({pct:.0f}%)", end="", flush=True)
        else:
            print(f"\r  {table}: {done:,} rows", end="", flush=True)

    registry = BackupRegistry()

    if args.backup_command == "list":
        records = registry.list_backups(
            connection=args.conn, database=args.database, table=args.table,
        )
        if not records:
            print("No backups found.")
            return 0
        print(f"{'ID':<6} {'Connection':<14} {'Database':<14} {'Table':<18} {'Date':<20} {'Rows':>10} {'Size':>10} {'Fmt'}")
        print("-" * 105)
        for r in records:
            size = _fmt_size(r.file_size)
            print(f"{r.id:<6} {r.connection:<14} {r.database:<14} {r.table:<18} {r.timestamp:<20} {r.row_count:>10,} {size:>10} {r.format}")
        return 0

    if args.backup_command == "delete":
        if registry.delete(args.id):
            print(f"Backup {args.id} deleted.")
        else:
            print(f"Backup {args.id} not found.", file=sys.stderr)
            return 1
        return 0

    conn = cfg.get_connection(args.conn)
    engine = BackupEngine(conn, registry)

    if args.backup_command == "table":
        print(f"Backing up {args.database}.{args.table} ({args.fmt})...")
        result = engine.backup_table(args.database, args.table,
                                     fmt=args.fmt, batch_size=args.batch_size,
                                     on_progress=_progress)
        print()
        _print_backup_result(result)
        return 0

    if args.backup_command == "db":
        print(f"Backing up database {args.database} ({args.fmt}, {args.threads} threads)...")
        results = engine.backup_database(args.database, fmt=args.fmt,
                                         batch_size=args.batch_size,
                                         threads=args.threads,
                                         on_progress=_progress)
        print()
        for r in results:
            _print_backup_result(r)
        print(f"\n{len(results)} table(s) backed up.")
        return 0

    if args.backup_command == "instance":
        print(f"Backing up instance {args.conn} ({args.fmt}, {args.threads} threads)...")
        results = engine.backup_instance(fmt=args.fmt, batch_size=args.batch_size,
                                         threads=args.threads,
                                         on_progress=_progress)
        print()
        for r in results:
            _print_backup_result(r)
        print(f"\n{len(results)} table(s) backed up.")
        return 0

    raise AssertionError(args.backup_command)


def _print_backup_result(result: dict) -> None:
    if result.get("error"):
        print(f"  FAIL  {result.get('database', '')}.{result.get('table', '')}: {result['error']}")
        return
    size = _fmt_size(result.get("file_size", 0))
    print(f"  OK    {result['database']}.{result['table']}  {result['row_count']:,} rows  {size}  → {result['file_path']}")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def dispatch_mcp(args: argparse.Namespace, _cfg: ConfigManager) -> int:
    from dbaide.mcp_server import serve
    serve(mode=getattr(args, "mode", "full"))
    return 0


def dispatch_setup(args: argparse.Namespace, _cfg: ConfigManager) -> int:
    from dbaide.skill import setup_tool, setup_all, uninstall_tool, uninstall_all, SUPPORTED_TOOLS

    mode = getattr(args, "mode", "full")

    if args.uninstall:
        if args.setup_all:
            removed = uninstall_all()
            if removed:
                for t in removed:
                    print(f"  removed: {t}")
                print(f"\n{len(removed)} tool(s) unregistered.")
            else:
                print("nothing to remove.")
            return 0
        tool = (args.tool or "").lower().strip()
        if not tool:
            print("specify a tool name or use --all", file=sys.stderr)
            return 1
        if uninstall_tool(tool):
            print(f"removed dbaide from {tool}")
        else:
            print(f"dbaide was not registered in {tool}")
        return 0

    mode_desc = {
        "full": "full (ask + atomic tools)",
        "ask": "ask (AI pipeline only)",
        "tools": "tools (atomic DB tools only)",
    }

    if args.setup_all:
        results = setup_all(mode=mode)
        for tool, path in results.items():
            print(f"  {tool:<12} → {path}")
        print(f"\n{len(results)} tools configured — mode: {mode_desc[mode]}")
        return 0

    tool = (args.tool or "").lower().strip()
    if not tool:
        print("specify a tool name or use --all", file=sys.stderr)
        print(f"supported: {', '.join(SUPPORTED_TOOLS)}", file=sys.stderr)
        return 1
    if tool not in SUPPORTED_TOOLS:
        print(f"unknown tool: {tool}", file=sys.stderr)
        print(f"supported: {', '.join(SUPPORTED_TOOLS)}", file=sys.stderr)
        return 1

    path = setup_tool(tool, mode=mode)
    print(f"  → {path}")
    print(f"\n{tool} integration ready — mode: {mode_desc[mode]}")
    return 0


def dispatch_queries(args: argparse.Namespace, cfg: ConfigManager) -> int:
    from dbaide.observability import query_log

    conn = cfg.get_connection(args.conn or None)
    log = query_log.for_instance(conn.name)
    entries = log.tail_file(limit=args.tail)
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2, default=str))
        return 0
    if not entries:
        print(f"No query log found for {conn.name}. (logs live under ~/.dbaide/logs/queries/)")
        return 0
    import datetime
    total_ms = 0.0
    print(f"{'Time':<20} {'Caller':<8} {'ms':>8} {'rows':>6}  SQL")
    print("-" * 90)
    for e in entries:
        ts = datetime.datetime.fromtimestamp(e.get("ts", 0)).strftime("%Y-%m-%d %H:%M:%S")
        sql = " ".join(str(e.get("sql", "")).split())
        if len(sql) > 60:
            sql = sql[:57] + "..."
        status = "" if e.get("status") == "ok" else " [ERR]"
        total_ms += float(e.get("elapsed_ms") or 0)
        print(f"{ts:<20} {e.get('caller',''):<8} {float(e.get('elapsed_ms') or 0):>8.1f} {int(e.get('row_count') or 0):>6}  {sql}{status}")
    print("-" * 90)
    print(f"{len(entries)} queries · total {total_ms:.0f}ms")
    return 0


def dispatch_annotate(args: argparse.Namespace, cfg: ConfigManager) -> int:
    from dbaide.annotations import AnnotationStore

    conn = cfg.get_connection(args.conn or None)
    store = AnnotationStore()

    if args.annotate_command == "add":
        scope = "column" if args.column else ("table" if args.table else "database")
        record = store.add(
            conn.name,
            scope=scope,
            note=args.note,
            database=args.database,
            table=args.table,
            column=args.column,
        )
        print(f"Saved {scope} note on {_annotation_label(record)}  (id {record['id']})")
        return 0

    if args.annotate_command == "list":
        records = store.list_records(conn.name, database=args.database, table=args.table)
        if getattr(args, "json", False):
            print(json.dumps(records, ensure_ascii=False, indent=2, default=str))
            return 0
        if not records:
            print(f"No notes for {conn.name}. Add one with: dbaide annotate add \"...\" --conn {conn.name} --table T")
            return 0
        for r in records:
            print(f"{r['id']}  {r['scope']:<8} {_annotation_label(r):<40}  {r['note']}")
        return 0

    if args.annotate_command == "rm":
        if args.id:
            ok = store.delete(conn.name, ann_id=args.id)
        else:
            scope = "column" if args.column else ("table" if args.table else "database")
            ok = store.delete(
                conn.name, scope=scope, database=args.database, table=args.table, column=args.column
            )
        print("Removed." if ok else "No matching note found.")
        return 0 if ok else 1

    return 1


def _annotation_label(record: dict) -> str:
    db = str(record.get("database") or "").strip()
    table = str(record.get("table") or "").strip()
    column = str(record.get("column") or "").strip()
    parts = [p for p in (db, table, column) if p]
    return ".".join(parts) if parts else "(connection-wide)"


def dispatch_connect(args: argparse.Namespace, cfg: ConfigManager) -> int:
    if args.connect_command == "add":
        conn = ConnectionConfig(
            name=args.name,
            type=args.type,
            database=args.database,
            path=args.path,
            host=args.host,
            port=args.port,
            user=args.user,
            password_env=args.password_env,
            password=args.password,
            load_profile=getattr(args, "load_profile", "production"),
            session_timezone=getattr(args, "session_timezone", "UTC"),
            sslmode=getattr(args, "sslmode", ""),
            ssl_ca=getattr(args, "ssl_ca", ""),
            table_allow=getattr(args, "table_allow", []),
            table_deny=getattr(args, "table_deny", []),
        )
        cfg.upsert_connection(conn, make_default=args.default)
        tls = f", sslmode={conn.sslmode}" if conn.sslmode else ""
        print(f"saved connection: {args.name} (load_profile={conn.load_profile}, session_timezone={conn.session_timezone}{tls})")
        if not args.skip_assets:
            build_connection_assets(
                cfg,
                conn,
                databases=args.asset_database or None,
                sample=not args.no_sample,
                profile=not args.no_profile,
                profile_mode=None if args.no_profile else args.profile_mode,
                top_k=args.top_k,
                sample_limit=args.sample_limit,
                timeout=getattr(args, "timeout", 0),
                max_workers=getattr(args, "max_workers", None),
                dry_run=getattr(args, "dry_run", False),
            )
        return 0
    if args.connect_command == "list":
        conns = cfg.connections()
        if not conns:
            print("No connections configured. Run `dbaide connect add <name> --type <type>` to add one.")
            return 0
        default = cfg._data.get("default_connection", "")
        print(f"{'Name':<20} {'Type':<12} {'Target':<40} {'Default'}")
        print("-" * 80)
        for name, conn in conns.items():
            if conn.type == "sqlite":
                target = conn.path or "(no path)"
            else:
                host = conn.host or "localhost"
                port = f":{conn.port}" if conn.port else ""
                target = f"{host}{port}/{conn.database or '(no database)'}"
            is_default = " *" if name == default else ""
            print(f"{name:<20} {conn.type:<12} {target:<40} {is_default}")
        return 0
    if args.connect_command == "test":
        conn = cfg.get_connection(args.name)
        adapter = build_adapter(conn)
        adapter.test()
        print(f"ok: {conn.name}")
        return 0
    raise AssertionError(args.connect_command)


def dispatch_assets(args: argparse.Namespace, cfg: ConfigManager) -> int:
    if args.assets_command == "build":
        conn = cfg.get_connection(args.conn)
        build_connection_assets(
            cfg,
            conn,
            databases=args.database or None,
            sample=not args.no_sample,
            profile=not args.no_profile,
            profile_mode=None if args.no_profile else args.profile_mode,
            top_k=args.top_k,
            sample_limit=args.sample_limit,
            timeout=args.timeout,
            per_column_timeout=args.per_column_timeout,
            max_workers=getattr(args, "max_workers", None),
            dry_run=getattr(args, "dry_run", False),
            load_profile_override=getattr(args, "load_profile", None),
        )
        return 0
    if args.assets_command == "status":
        store = AssetStore()
        conns = cfg.connections()
        selected = [cfg.get_connection(args.conn)] if args.conn else list(conns.values())
        if not selected:
            print("No connections configured.")
            return 0
        print(f"{'Instance':<20} {'Status':<10} {'DBs':<6} {'Tables':<8} {'Columns':<8} {'Built'}")
        print("-" * 70)
        for conn in selected:
            doc = store.instance_doc(conn.name)
            if not doc:
                print(f"{conn.name:<20} {'missing':<10}")
                continue
            dbs = doc.get("databases") or []
            stats = doc.get("stats") or {}
            built = doc.get("completed_at") or doc.get("built_at")
            built_str = ""
            if built:
                import datetime
                built_str = datetime.datetime.fromtimestamp(built).strftime("%Y-%m-%d %H:%M")
            print(f"{conn.name:<20} {'ready':<10} {len(dbs):<6} {stats.get('tables', '?'):<8} {stats.get('columns', '?'):<8} {built_str}")
        return 0
    if args.assets_command == "show":
        store = AssetStore()
        parts = [p for p in args.path.split(".") if p]
        if len(parts) == 1:
            doc = store.instance_doc(parts[0])
        elif len(parts) == 2:
            db_path = store.database_dir(parts[0], parts[1]) / "database.json"
            doc = store._read_optional(db_path)
        elif len(parts) >= 3:
            table = ".".join(parts[2:])
            doc = store.table_doc(parts[0], parts[1], table)
            if doc is None:
                table = ".".join(parts[2:-1])
                doc = store.table_doc(parts[0], parts[1], table)
        else:
            raise ValueError("asset path must be instance, instance.database, instance.database.table, or instance.database.table.column")
        if doc is None:
            print(f"asset not found: {args.path}", file=sys.stderr)
            return 1
        print(json.dumps(doc, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.assets_command == "enrich":
        conn = cfg.get_connection(args.conn)
        enrich_assets(cfg, conn, database=args.database, table=args.table, columns=[c.strip() for c in args.columns.split(",") if c.strip()], top_k=args.top_k, sample_limit=args.sample_limit)
        return 0
    raise AssertionError(args.assets_command)


def build_connection_assets(
    cfg: ConfigManager,
    conn: ConnectionConfig,
    *,
    databases: list[str] | None,
    sample: bool,
    profile: bool,
    profile_mode: str | None = None,
    top_k: int = 30,
    sample_limit: int = 50,
    timeout: int = 0,
    per_column_timeout: int = 30,
    max_workers: int | None = None,
    dry_run: bool = False,
    load_profile_override: str | None = None,
) -> None:
    if load_profile_override:
        from dbaide.db.policy import resolve_policy
        policy = resolve_policy(load_profile=load_profile_override, overrides=cfg.resource_defaults())
    else:
        policy = cfg.policy_for(conn)
    adapter = build_adapter(conn, policy=policy, caller="build")
    try:
        llm = build_llm_client(cfg.model())
    except Exception:
        llm = NullLLMClient()
    def _print_progress(msg) -> None:
        # The builder now emits structured dict events for the GUI trace; the CLI
        # just prints their human-readable title.
        if isinstance(msg, dict):
            title = str(msg.get("title") or "")
            if title:
                print(f"[assets] {title}")
        else:
            print(msg)

    builder = AssetBuilder(connection=conn, adapter=adapter, llm=llm,
                           join_catalog=JoinCatalogStore(), progress=_print_progress)
    effective_mode = "none" if not profile else profile_mode
    stats = builder.build(
        databases=databases, sample=sample, profile_mode=effective_mode,
        top_k=top_k, sample_limit=sample_limit,
        timeout=timeout, per_column_timeout=per_column_timeout,
        max_workers=max_workers, dry_run=dry_run,
    )
    if dry_run:
        print(
            f"[assets] dry-run: tables={stats.tables}, columns={stats.columns}, "
            f"would-profile={stats.profiled_columns}, estimated_queries≈{stats.estimated_queries}"
        )
        return
    print(
        f"[assets] executed {stats.total_queries} queries (peak in-flight {stats.peak_inflight}), "
        f"profiled {stats.profiled_columns} column(s), {stats.light_tables} large table(s) profiled light"
    )
    if stats.errors:
        print(f"[assets] {len(stats.errors)} warning(s):", file=sys.stderr)
        for item in stats.errors[:20]:
            print(f"  - {item}", file=sys.stderr)
        if len(stats.errors) > 20:
            print(f"  ... and {len(stats.errors) - 20} more", file=sys.stderr)


def enrich_assets(
    cfg: ConfigManager,
    conn: ConnectionConfig,
    *,
    database: str,
    table: str,
    columns: list[str],
    top_k: int,
    sample_limit: int,
) -> None:
    adapter = build_adapter(conn)
    try:
        llm = build_llm_client(cfg.model())
    except Exception:
        llm = NullLLMClient()
    store = AssetStore()
    stats = AssetBuilder(connection=conn, adapter=adapter, store=store, llm=llm).build(
        databases=[database],
        tables=[table],
        sample=True,
        profile_mode="none",
        top_k=top_k,
        sample_limit=sample_limit,
    )
    if columns:
        print("[assets-warning] column-specific enrich is no longer stored offline; use column_stats on demand.", file=sys.stderr)
    print(f"enriched table asset: {conn.name}.{database}.{table} ({stats.columns} column(s))")


def dispatch_find(args: argparse.Namespace, cfg: ConfigManager) -> int:
    conns = cfg.connections()
    if not conns:
        raise ValueError("No connections configured. Run `dbaide connect add ...` first.")
    if args.conn.strip().lower() == "all":
        instances = list(conns.keys())
    elif args.conn.strip():
        instances = [name.strip() for name in args.conn.split(",") if name.strip()]
    else:
        instances = [cfg.get_connection(None).name]
    hits = AssetSearch().search(args.query, instances=instances, limit=args.limit)
    if args.json:
        def _hit_to_dict(hit):
            return {slot: getattr(hit, slot) for slot in hit.__slots__}
        print(json.dumps([_hit_to_dict(hit) for hit in hits], ensure_ascii=False, indent=2, default=str))
        return 0
    if not hits:
        print("No matching asset documents found.")
        return 0
    for hit in hits:
        meta = []
        if hit.metadata.get("data_type"):
            meta.append(str(hit.metadata["data_type"]))
        if hit.metadata.get("role"):
            meta.append(str(hit.metadata["role"]))
        if hit.metadata.get("profile_status"):
            meta.append(str(hit.metadata["profile_status"]))
        suffix = f" [{' / '.join(meta)}]" if meta else ""
        print(f"{hit.kind}\t{hit.path}\tscore={hit.score:.1f}{suffix}")
        if hit.summary:
            print(f"  {hit.summary}")
    return 0


def build_adapter_session(cfg: ConfigManager, args: argparse.Namespace):
    conn = cfg.get_connection(args.conn or None)
    policy = cfg.policy_for(conn)
    adapter = build_adapter(conn, policy=policy, caller="cli")
    session = Session.from_policy(
        conn, policy, default_limit=args.limit, timeout_seconds=args.timeout,
    )
    _populate_disclosure(adapter, session, conn.name, args.database)
    return adapter, session


def run_workflow_cli(cfg: ConfigManager, args: argparse.Namespace):
    targets = resolve_targets(cfg, args.conn, args.database)
    if len(targets) != 1:
        # Cross-instance fan-out: the MultiInstanceAssistant already queries every
        # target. Build the result straight from its merged response — do NOT also
        # run a single-instance WorkflowEngine (that re-executed the query against
        # targets[0] and returned its trace/JSON, inconsistent with the answer).
        assistant = build_any_assistant(cfg, args)
        response = assistant.ask(args.question, database=args.database, execute=True)
        result = WorkflowResult(
            status=WorkflowStatus.COMPLETED,
            question=args.question,
            connection_name=", ".join(t.config.name for t in targets),
            database_scope=[t.database for t in targets if t.database],
            answer_markdown=response.answer,
            answer_plaintext=response.answer,
            selected_sql=response.sql,
            execution_result=response.result,
            warnings=response.warnings,
        )
        result.charts = list(response.charts or [])
        result.executed_sqls = list(response.executed_sqls or [])
        return result

    target = targets[0]
    return WorkflowEngine(target.config, llm=safe_llm(cfg), asset_store=AssetStore(),
                          model_config=cfg.model()).run(
        WorkflowRequest(
            question=args.question,
            connection_name=target.config.name,
            database_scope=[target.database] if target.database else [],
            limit=args.limit,
            timeout_seconds=args.timeout,
        )
    )


def safe_llm(cfg: ConfigManager):
    try:
        return build_llm_client(cfg.model())
    except Exception:
        return NullLLMClient()


def _populate_disclosure(adapter, session: Session, instance: str, database: str) -> None:
    dc = session.disclosure
    dc.set_instance(instance)
    try:
        databases = adapter.list_databases()
        dc.record_databases(databases)
    except Exception as exc:
        logger.debug("list_databases failed for %s: %s", instance, exc)
        databases = []
    active_db = database or (databases[0] if len(databases) == 1 else "")
    try:
        tables = adapter.list_tables(database=active_db)
        dc.record_tables(tables, database=active_db)
    except Exception as exc:
        logger.debug("list_tables failed for %s.%s: %s", instance, active_db, exc)


def build_any_assistant(cfg: ConfigManager, args: argparse.Namespace):
    targets = resolve_targets(cfg, args.conn, args.database)
    llm = build_llm_client(cfg.model())
    if len(targets) == 1:
        conn = targets[0].config
        policy = cfg.policy_for(conn)
        adapter = build_adapter(conn, policy=policy, caller="agent")
        session = Session.from_policy(
            conn, policy, default_limit=args.limit, timeout_seconds=args.timeout,
        )
        model_cfg = cfg.model()
        assistant = DataAssistant(adapter, session, llm, model_config=model_cfg)
        return _SingleAssistantWithDatabase(assistant, targets[0].database)
    return _MultiAssistantWithDatabase(
        MultiInstanceAssistant(targets, llm, default_limit=args.limit, timeout_seconds=args.timeout,
                               model_config=cfg.model())
    )


def resolve_targets(cfg: ConfigManager, conn_spec: str, database_spec: str) -> list[InstanceTarget]:
    conns = cfg.connections()
    if not conns:
        raise ValueError("No connections configured. Run `dbaide connect add ...` first.")
    if not conn_spec:
        selected = [cfg.get_connection(None)]
    elif conn_spec.strip().lower() == "all":
        selected = list(conns.values())
    else:
        selected = [cfg.get_connection(name.strip()) for name in conn_spec.split(",") if name.strip()]
    db_map = _parse_database_spec(database_spec)
    targets: list[InstanceTarget] = []
    for conn in selected:
        wanted = db_map.get(conn.name, db_map.get("", ""))
        if wanted.lower() == "all":
            adapter = build_adapter(conn)
            for database in adapter.list_databases():
                targets.append(InstanceTarget(config=conn, database=database))
        else:
            targets.append(InstanceTarget(config=conn, database=wanted))
    return targets


def _chat_turn(assistant, question: str, *, database: str) -> None:
    """Run one interactive chat turn, handling clarification/risk pauses by
    prompting for a reply and resuming, until the agent produces a final answer."""
    response = assistant.ask(question, database=database, execute=True)
    while getattr(response, "status", "completed") == "wait_user":
        pending = getattr(response, "pending_question", "") or response.answer or "Please clarify"
        print(pending)
        options = list(getattr(response, "pending_options", None) or [])
        if options:
            for i, opt in enumerate(options, start=1):
                print(f"  {i}. {opt}")
        resume_state = getattr(response, "resume_state", None)
        if not resume_state:
            # No way to resume — surface the answer and stop (avoid an infinite loop).
            print(response.answer)
            return
        try:
            reply = input("reply> ").strip()
        except EOFError:
            print()
            return
        if not reply or reply.lower() in EXIT_WORDS:
            print("(cancelled)")
            return
        response = assistant.ask(
            question, database=database, execute=True,
            resume_state=resume_state, user_reply=reply,
        )
    print(response.answer)


class _SingleAssistantWithDatabase:
    def __init__(self, assistant: DataAssistant, database: str) -> None:
        self.assistant = assistant
        self.database = database

    def ask(self, question: str, *, database: str = "", execute: bool = True,
            resume_state: dict | None = None, user_reply: str = ""):
        return self.assistant.ask(
            question, database=database or self.database, execute=execute,
            resume_state=resume_state, user_reply=user_reply,
        )


class _MultiAssistantWithDatabase:
    def __init__(self, assistant: MultiInstanceAssistant) -> None:
        self.assistant = assistant

    def ask(self, question: str, *, database: str = "", execute: bool = True,
            resume_state: dict | None = None, user_reply: str = ""):
        # Cross-instance fan-out has no clarification pause; resume args are ignored.
        return self.assistant.ask(question, execute=execute)


def _parse_database_spec(spec: str) -> dict[str, str]:
    if not spec:
        return {}
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            conn, db = part.split("=", 1)
            out[conn.strip()] = db.strip()
        else:
            out[""] = part
    return out


def _parse_asset_root(value: str) -> tuple[str, str]:
    parts = [p for p in value.split(".") if p]
    if not parts:
        raise ValueError("asset root cannot be empty")
    return parts[0], parts[1] if len(parts) > 1 else ""


def print_diff(payload: dict) -> None:
    missing_left = payload.get("missing_tables_left") or []
    missing_right = payload.get("missing_tables_right") or []
    column_diffs = payload.get("column_diffs") or []
    if not missing_left and not missing_right and not column_diffs:
        print("No schema differences found.")
        return
    if missing_left:
        print("Tables only on right:")
        for item in missing_left:
            print(f"- {item}")
    if missing_right:
        print("Tables only on left:")
        for item in missing_right:
            print(f"- {item}")
    if column_diffs:
        print("Column differences:")
        for item in column_diffs:
            print(f"- {item.get('table', '?')}")
            for col in item.get("missing_left", []):
                print(f"  + right only column: {col}")
            for col in item.get("missing_right", []):
                print(f"  - left only column: {col}")
            for change in item.get("type_changes", []):
                print(f"  * {change.get('column', '?')}: {change.get('left_type', '?')} -> {change.get('right_type', '?')}")


def print_result(result: QueryResult) -> None:
    if not result.rows:
        print("(empty)")
        return
    cols = result.columns or (list(result.rows[0].keys()) if result.rows else [])
    if not cols:
        print("(no columns)")
        return
    widths = {c: min(max(len(c), *(len(str(row.get(c, ""))) for row in result.rows[:50])), 40) for c in cols}
    print(" | ".join(c.ljust(widths[c]) for c in cols))
    print("-+-".join("-" * widths[c] for c in cols))
    for row in result.rows:
        print(" | ".join(str(row.get(c, ""))[: widths[c]].ljust(widths[c]) for c in cols))


def write_result(result: QueryResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(result.rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=result.columns)
        writer.writeheader()
        writer.writerows(result.rows)


if __name__ == "__main__":
    raise SystemExit(main())
