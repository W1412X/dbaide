"""Minimal MCP (Model Context Protocol) server exposing a single ``ask`` tool.

Speaks JSON-RPC 2.0 over stdio — compatible with Claude Code, Cursor, Windsurf,
Cline, Roo, and any other MCP-capable agent.

Start with::

    dbaide mcp            # via the CLI entry point
    python -m dbaide.mcp_server   # direct

The server advertises one tool:

    ask(question, conn?, database?)

which runs the full DBAide agent pipeline and returns the answer.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from typing import Any

logger = logging.getLogger("dbaide.mcp")

# ── Protocol constants ──────────────────────────────────────────────────────

JSONRPC = "2.0"
SERVER_NAME = "dbaide"
PROTOCOL_VERSION = "2024-11-05"


def _server_version() -> str:
    try:
        from dbaide import __version__
        return __version__
    except Exception:
        return "0.0.0"


ASK_TOOL = {
    "name": "ask",
    "description": (
        "Ask a natural-language question about the database. "
        "DBAide generates SQL, executes it, and returns a formatted answer with the query used."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Natural language question about the database",
            },
            "conn": {
                "type": "string",
                "description": "Connection name (omit to use the default connection)",
            },
            "database": {
                "type": "string",
                "description": "Database/schema name (optional)",
            },
        },
        "required": ["question"],
    },
}

# ── JSON-RPC helpers ────────────────────────────────────────────────────────

def _ok(id: Any, result: Any) -> dict:
    return {"jsonrpc": JSONRPC, "id": id, "result": result}


def _error(id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC, "id": id, "error": err}


# ── Handlers ────────────────────────────────────────────────────────────────

def handle_initialize(params: dict) -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": _server_version()},
    }


def handle_tools_list(_params: dict) -> dict:
    return {"tools": [ASK_TOOL]}


def handle_tools_call(params: dict) -> dict:
    name = params.get("name", "")
    if name != "ask":
        raise ValueError(f"Unknown tool: {name}")

    arguments = params.get("arguments") or {}
    question = str(arguments.get("question") or "").strip()
    if not question:
        return {"content": [{"type": "text", "text": "Error: question is required"}], "isError": True}

    conn = str(arguments.get("conn") or "").strip() or None
    database = str(arguments.get("database") or "").strip()

    try:
        from dbaide.config import ConfigManager
        from dbaide.assets import AssetStore
        from dbaide.llm import build_llm_client
        from dbaide.core.workflow import WorkflowEngine
        from dbaide.core.result import WorkflowRequest

        cfg = ConfigManager()
        connection = cfg.get_connection(conn)
        llm = build_llm_client(cfg.model())
        store = AssetStore()

        result = WorkflowEngine(connection, llm=llm, asset_store=store).run(
            WorkflowRequest(
                question=question,
                connection_name=connection.name,
                database_scope=[database] if database else [],
            )
        )

        parts: list[str] = []
        answer = result.answer_markdown or result.answer_plaintext or ""
        if answer:
            parts.append(answer)
        if result.selected_sql:
            parts.append(f"\n```sql\n{result.selected_sql}\n```")
        if result.warnings:
            parts.append("\n**Warnings:** " + "; ".join(result.warnings))

        text = "\n".join(parts) if parts else "(no answer)"
        return {"content": [{"type": "text", "text": text}]}

    except Exception as exc:
        logger.exception("ask tool failed")
        return {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}


HANDLERS = {
    "initialize": handle_initialize,
    "notifications/initialized": None,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


# ── Main loop ───────────────────────────────────────────────────────────────

def _send(msg: dict) -> None:
    try:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        raise SystemExit(0)


def serve() -> None:
    """Run the MCP server on stdio (blocking)."""
    # Redirect logging to stderr so it never contaminates the JSON-RPC stream.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Graceful shutdown on SIGTERM (e.g. when the client kills the process).
    signal.signal(signal.SIGTERM, lambda *_: raise_exit())

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            method = msg.get("method", "")
            msg_id = msg.get("id")
            params = msg.get("params") or {}

            handler = HANDLERS.get(method)
            if handler is None:
                if msg_id is not None and method not in HANDLERS:
                    _send(_error(msg_id, -32601, f"Method not found: {method}"))
                continue

            try:
                result = handler(params)
                if msg_id is not None:
                    _send(_ok(msg_id, result))
            except Exception as exc:
                logger.exception("handler error for %s", method)
                if msg_id is not None:
                    _send(_error(msg_id, -32603, str(exc)))

    except (KeyboardInterrupt, SystemExit):
        pass
    except (BrokenPipeError, OSError):
        pass


def raise_exit() -> None:
    raise SystemExit(0)


if __name__ == "__main__":
    serve()
