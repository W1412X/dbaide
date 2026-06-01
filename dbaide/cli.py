from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from dbaide.adapters import build_adapter
from dbaide.agent import DataAssistant, InstanceTarget, MultiInstanceAssistant
from dbaide.assets import AssetBuilder, AssetSearch, AssetStore
from dbaide.assets.profiler import ColumnProfiler
from dbaide.assets.summarizer import AssetSummarizer
from dbaide.config import ConfigManager
from dbaide.core.result import ExecutionPolicy, WorkflowRequest
from dbaide.core.workflow import WorkflowEngine
from dbaide.history.debug_bundle import create_debug_bundle
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
    if quiet:
        level = logging.ERROR
    elif verbosity >= 2:
        level = logging.DEBUG
    elif verbosity >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


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
    ask.add_argument("--no-execute", action="store_true")
    ask.add_argument("--show-disclosure", action="store_true")
    ask.add_argument("--show-trace", action="store_true")
    ask.add_argument("--policy", choices=["inspect-only", "sql-only", "safe-auto", "expert"], default="safe-auto")
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
    parser.add_argument("--timeout", type=lambda v: _bounded_int(v, min_val=1, max_val=3600, name="timeout"), default=10)


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
            if args.show_trace:
                print("\nTrace:")
                for event in result.trace:
                    print(f"- {event.kind.value}:{event.stage} {event.title}")
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
    if args.command == "doc":
        conn = cfg.get_connection(args.conn or None)
        text = DeveloperTools().markdown(conn.name, database=args.database)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
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
            response = assistant.ask(question, database=args.database, execute=True)
            print(response.answer)
    if args.command == "inspect":
        adapter, session = build_adapter_session(cfg, args)
        schema = SchemaTools(adapter, session.disclosure)
        info = schema.inspect_table(args.table, database=args.database)
        print(_format_inspect(info))
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
    raise AssertionError(args.command)


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
        )
        cfg.upsert_connection(conn, make_default=args.default)
        print(f"saved connection: {args.name} (load_profile={conn.load_profile})")
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
        elif len(parts) == 3:
            doc = store.table_doc(parts[0], parts[1], parts[2])
        elif len(parts) == 4:
            col_path = store.column_dir(parts[0], parts[1], parts[2]) / f"{parts[3]}.json"
            doc = store._read_optional(col_path)
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
    builder = AssetBuilder(connection=conn, adapter=adapter, llm=llm, progress=print)
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
    summarizer = AssetSummarizer(llm)
    profiler = ColumnProfiler(adapter)
    table_info = next((t for t in adapter.list_tables(database=database) if t.name == table), None)
    if table_info is None:
        raise ValueError(f"table not found: {database}.{table}")
    column_infos = adapter.describe_table(table, database=database)
    available = {col.name for col in column_infos}
    selected = set(columns) if columns else available
    if columns:
        missing = selected - available
        if missing:
            print(f"[assets-warning] columns not found in {database}.{table}: {', '.join(sorted(missing))}", file=sys.stderr)
            selected &= available
    updated = 0
    for column in column_infos:
        if column.name not in selected:
            continue
        profile = profiler.profile(table, column, database=database, top_k=top_k, sample_limit=sample_limit)
        doc = summarizer.column_doc(instance=conn.name, database=database, table=table, column=column, profile=profile)
        store.write_json(store.column_dir(conn.name, database, table) / f"{column.name}.json", doc)
        updated += 1
    column_docs = store.column_docs(conn.name, database, table)
    fks = adapter.foreign_keys(table, database=database)
    table_doc = summarizer.table_doc(instance=conn.name, database=database, table=table_info, columns=column_docs, foreign_keys=fks)
    try:
        table_doc["sample_rows"] = adapter.sample_rows(table, database=database, limit=min(sample_limit, 50)).rows
    except Exception:
        table_doc["sample_rows"] = []
    table_doc["column_count"] = len(column_docs)
    store.write_json(store.table_dir(conn.name, database, table) / "table.json", table_doc)
    store.write_json(store.table_dir(conn.name, database, table) / "columns.json", {"instance": conn.name, "database": database, "table": table, "columns": column_docs})
    print(f"enriched {updated} column(s): {conn.name}.{database}.{table}")


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
    adapter = build_adapter(conn, policy=cfg.policy_for(conn), caller="cli")
    session = Session(connection=conn, default_limit=args.limit, timeout_seconds=args.timeout)
    _populate_disclosure(adapter, session, conn.name, args.database)
    return adapter, session


def run_workflow_cli(cfg: ConfigManager, args: argparse.Namespace):
    targets = resolve_targets(cfg, args.conn, args.database)
    if len(targets) != 1:
        assistant = build_any_assistant(cfg, args)
        response = assistant.ask(args.question, database=args.database, execute=not args.no_execute)
        result = WorkflowEngine(targets[0].config, llm=safe_llm(cfg), asset_store=AssetStore()).run(
            WorkflowRequest(
                question=args.question,
                connection_name=targets[0].config.name,
                database_scope=[targets[0].database] if targets[0].database else [],
                execution_policy=ExecutionPolicy.SQL_ONLY if args.no_execute else _cli_policy(args.policy),
                limit=args.limit,
                timeout_seconds=args.timeout,
                show_trace=True,
            )
        )
        result.answer_markdown = response.answer
        result.answer_plaintext = response.answer
        result.warnings = response.warnings
        if response.sql:
            result.selected_sql = response.sql
        if response.result:
            result.execution_result = response.result
        return result

    target = targets[0]
    policy = ExecutionPolicy.SQL_ONLY if args.no_execute else _cli_policy(args.policy)
    return WorkflowEngine(target.config, llm=safe_llm(cfg), asset_store=AssetStore()).run(
        WorkflowRequest(
            question=args.question,
            connection_name=target.config.name,
            database_scope=[target.database] if target.database else [],
            execution_policy=policy,
            limit=args.limit,
            timeout_seconds=args.timeout,
            show_trace=True,
        )
    )


def safe_llm(cfg: ConfigManager):
    try:
        return build_llm_client(cfg.model())
    except Exception:
        return NullLLMClient()


def _cli_policy(value: str) -> ExecutionPolicy:
    normalized = value.replace("-", "_")
    for policy in ExecutionPolicy:
        if policy.value == normalized:
            return policy
    return ExecutionPolicy.SAFE_AUTO


def _populate_disclosure(adapter, session: Session, instance: str, database: str) -> None:
    dc = session.disclosure
    dc.record_instances([instance])
    try:
        databases = adapter.list_databases()
        dc.record_databases(instance, databases)
    except Exception as exc:
        logger.debug("list_databases failed for %s: %s", instance, exc)
        databases = []
    active_db = database or (databases[0] if len(databases) == 1 else "")
    try:
        tables = adapter.list_tables(database=active_db)
        dc.record_tables(tables, instance=instance, database=active_db)
    except Exception as exc:
        logger.debug("list_tables failed for %s.%s: %s", instance, active_db, exc)


def build_assistant(cfg: ConfigManager, args: argparse.Namespace):
    adapter, session = build_adapter_session(cfg, args)
    llm = build_llm_client(cfg.model())
    return DataAssistant(adapter, session, llm), adapter, session


def build_any_assistant(cfg: ConfigManager, args: argparse.Namespace):
    targets = resolve_targets(cfg, args.conn, args.database)
    llm = build_llm_client(cfg.model())
    if len(targets) == 1:
        conn = targets[0].config
        adapter = build_adapter(conn, policy=cfg.policy_for(conn), caller="agent")
        session = Session(connection=conn, default_limit=args.limit, timeout_seconds=args.timeout)
        assistant = DataAssistant(adapter, session, llm)
        return _SingleAssistantWithDatabase(assistant, targets[0].database)
    return _MultiAssistantWithDatabase(
        MultiInstanceAssistant(targets, llm, default_limit=args.limit, timeout_seconds=args.timeout)
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


class _SingleAssistantWithDatabase:
    def __init__(self, assistant: DataAssistant, database: str) -> None:
        self.assistant = assistant
        self.database = database

    def ask(self, question: str, *, database: str = "", execute: bool = True):
        return self.assistant.ask(question, database=database or self.database, execute=execute)


class _MultiAssistantWithDatabase:
    def __init__(self, assistant: MultiInstanceAssistant) -> None:
        self.assistant = assistant

    def ask(self, question: str, *, database: str = "", execute: bool = True):
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
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(result.rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=result.columns)
        writer.writeheader()
        writer.writerows(result.rows)


def _response_json(response) -> dict:
    return {
        "answer": response.answer,
        "sql": response.sql,
        "rows": response.result.rows if response.result else None,
        "disclosures": response.disclosures,
        "warnings": response.warnings,
    }


def _format_inspect(info: dict) -> str:
    from dbaide.agent.assistant import format_inspect as _fmt
    return _fmt(info)


if __name__ == "__main__":
    raise SystemExit(main())
