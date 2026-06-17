"""HTTP API server wrapping DesktopService for Electron/web frontends.

Starts a local-only uvicorn server on a random port. The Electron main
process reads the port from stdout and connects to it.

Every DesktopService action is exposed as ``POST /api/{action}`` with a
JSON body that maps to the action's payload dict.  Long-running actions
stream progress via SSE on ``GET /api/{action}/stream``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService

logger = logging.getLogger("dbaide.server")

app = FastAPI(title="DBAide API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_service: DesktopService | None = None


def _get_service() -> DesktopService:
    global _service
    if _service is None:
        cfg = ConfigManager()
        _service = DesktopService(cfg)
    return _service


# ── Sync dispatch (short actions) ────────────────────────────────────────────


@app.post("/api/{action}")
async def dispatch(action: str, request: Request) -> JSONResponse:
    body = await request.json() if await request.body() else {}
    service = _get_service()
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: service.dispatch(action, body)
        )
        return JSONResponse(_safe_json(result))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("Action %s failed", action)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── SSE streaming (long-running actions with progress) ───────────────────────


_STREAM_ACTIONS = frozenset({
    "ask", "build_assets", "project_instance", "refresh_instance",
    "enrich_table", "execute_sql", "explain_sql", "browse_table",
    "count_table", "backup_run",
})


@app.post("/api/{action}/stream")
async def dispatch_stream(action: str, request: Request) -> StreamingResponse:
    if action not in _STREAM_ACTIONS:
        return StreamingResponse(
            _single_event({"error": f"Action '{action}' does not support streaming"}),
            media_type="text/event-stream",
        )

    body = await request.json() if await request.body() else {}
    service = _get_service()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def progress(msg: Any) -> None:
        asyncio.run_coroutine_threadsafe(
            queue.put({"type": "progress", "data": msg}), loop
        )

    def cancel_check() -> None:
        pass

    body["progress"] = progress
    body["cancel_check"] = cancel_check

    async def run_and_signal():
        try:
            result = await loop.run_in_executor(
                None, lambda: service.dispatch(action, body)
            )
            await queue.put({"type": "done", "data": _safe_json(result)})
        except Exception as exc:
            await queue.put({"type": "error", "data": str(exc)})
        await queue.put(None)

    asyncio.ensure_future(run_and_signal())

    async def event_stream():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ── Helpers ──────────────────────────────────────────────────────────────────


def _safe_json(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_json(v) for k, v in obj.items()}
    if hasattr(obj, "__dict__"):
        return {k: _safe_json(v) for k, v in obj.__dict__.items()}
    return str(obj)


async def _single_event(data: dict):
    yield f"data: {json.dumps(data)}\n\n"


def main() -> None:
    """Start the API server on a random port; print port to stdout."""
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=0,
        log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)

    # uvicorn with port=0 picks a free port. We need to print it for Electron.
    original_startup = server.startup

    async def startup_with_port(**kwargs):
        await original_startup(**kwargs)
        for sock in server.servers[0].sockets:
            port = sock.getsockname()[1]
            # Signal the port to the parent process (Electron)
            print(f"DBAIDE_PORT={port}", flush=True)
            break

    server.startup = startup_with_port
    server.run()


if __name__ == "__main__":
    main()
