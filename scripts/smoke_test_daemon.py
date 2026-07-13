#!/usr/bin/env python3
"""Smoke-test the frozen shared daemon without importing the GUI runtime."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health(port: int, token: str) -> dict | None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1) as response:
            return json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--wait", type=float, default=30)
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.is_file():
        parser.error(f"binary not found: {binary}")

    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="dbaide-daemon-smoke-") as raw_tmp:
        tmp = Path(raw_tmp)
        config = tmp / "config.toml"
        token_path = tmp / "mcp" / "token"
        env = os.environ.copy()
        env.update({
            "DBAIDE_CONFIG": str(config),
            "DBAIDE_ASSETS": str(tmp / "assets"),
            "DBAIDE_MCP_PORT": str(port),
            "DBAIDE_MCP_AUTOSTART": "0",
        })
        process = subprocess.Popen(
            [
                str(binary),
                "mcp-daemon",
                "run",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + args.wait
            health = None
            while time.monotonic() < deadline and process.poll() is None:
                if token_path.is_file():
                    token = token_path.read_text(encoding="utf-8").strip()
                    health = _health(port, token)
                    if health is not None:
                        break
                time.sleep(0.2)

            if health is None:
                output = process.stdout.read() if process.poll() is not None else ""
                raise RuntimeError(
                    f"daemon did not become healthy (exit={process.poll()}):\n{output}"
                )
            if health.get("desktopRpcVersion") != 1:
                raise RuntimeError(f"unexpected daemon health payload: {health}")
            if health.get("uiRuntimeLoaded") is not False:
                raise RuntimeError(f"daemon loaded the GUI runtime: {health}")
            if process.poll() is not None:
                raise RuntimeError(f"daemon exited early with {process.returncode}")
            print(
                "[ok] shared daemon healthy and headless "
                f"(pid={health.get('pid')}, port={port})"
            )
            return 0
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
