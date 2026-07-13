#!/usr/bin/env bash
# Verify the packaged GUI is the only foreground LaunchServices process.
# Usage: ./scripts/smoke_test_macos.sh dist/DBAide.app
set -euo pipefail

APP="${1:?Usage: smoke_test_macos.sh /path/to/App.app}"
WAIT="${2:-30}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "smoke_test_macos.sh: macOS only" >&2
  exit 1
fi
if [[ ! -d "$APP" ]]; then
  echo "smoke_test_macos.sh: not found: $APP" >&2
  exit 1
fi

ABS="$(cd "$(dirname "$APP")" && pwd)/$(basename "$APP")"
BIN="$ABS/Contents/MacOS/DBAide"
TMP="$(mktemp -d -t dbaide-gui-smoke)"
GUI_PID=""
DAEMON_PID=""
DAEMON_HEALTHY=0

cleanup() {
  if [[ -n "$DAEMON_PID" ]]; then kill "$DAEMON_PID" 2>/dev/null || true; fi
  if [[ -n "$GUI_PID" ]]; then kill "$GUI_PID" 2>/dev/null || true; fi
  rm -rf "$TMP"
}
trap cleanup EXIT

PORT="$(python - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"

echo "==> Launching isolated app bundle: $ABS"
DBAIDE_CONFIG="$TMP/config.toml" \
DBAIDE_ASSETS="$TMP/assets" \
DBAIDE_MCP_PORT="$PORT" \
DBAIDE_MCP_AUTOSTART=0 \
"$BIN" mcp-daemon run --host 127.0.0.1 --port "$PORT" \
  >"$TMP/daemon.log" 2>&1 &
DAEMON_PID=$!

for _ in $(seq 1 "$WAIT"); do
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    echo "✗ shared daemon exited early" >&2
    cat "$TMP/daemon.log" >&2
    exit 1
  fi
  if [[ -s "$TMP/mcp/token" ]]; then
    TOKEN="$(tr -d '\r\n' <"$TMP/mcp/token")"
    if curl -fsS -H "Authorization: Bearer $TOKEN" \
      "http://127.0.0.1:$PORT/health" >/dev/null; then
      DAEMON_HEALTHY=1
      break
    fi
  fi
  sleep 1
done

if [[ "$DAEMON_HEALTHY" != "1" ]]; then
  echo "✗ shared daemon did not become healthy" >&2
  cat "$TMP/daemon.log" >&2
  exit 1
fi

# The desktop is intentionally tested as a client of an already-running,
# externally managed backend. This keeps the smoke test from installing or
# modifying the real user's LaunchAgent.
open -n -o "$TMP/gui.log" --stderr "$TMP/gui.log" \
  --env "DBAIDE_CONFIG=$TMP/config.toml" \
  --env "DBAIDE_ASSETS=$TMP/assets" \
  --env "DBAIDE_MCP_PORT=$PORT" \
  --env "DBAIDE_MCP_AUTOSTART=0" \
  "$ABS"

for _ in $(seq 1 "$WAIT"); do
  GUI_PID="$(pgrep -f -x "$BIN" | head -1 || true)"
  if [[ -z "$GUI_PID" ]]; then
    sleep 1
    continue
  fi
  if ! kill -0 "$GUI_PID" 2>/dev/null; then
    echo "✗ GUI exited early" >&2
    cat "$TMP/gui.log" >&2
    exit 1
  fi
  GUI_LINE="$(/usr/bin/lsappinfo list | grep "pid = $GUI_PID " | head -1 || true)"
  if [[ -n "$GUI_LINE" ]]; then break; fi
  sleep 1
done

# Give LaunchServices a moment to settle after TransformProcessType.
sleep 2
APP_INFO="$(/usr/bin/lsappinfo list)"
GUI_LINE="$(printf '%s\n' "$APP_INFO" | grep "pid = $GUI_PID " | head -1 || true)"
DAEMON_LINE="$(printf '%s\n' "$APP_INFO" | grep "pid = $DAEMON_PID " | head -1 || true)"

echo "GUI:    $GUI_LINE"
echo "daemon: $DAEMON_LINE"
if [[ "$GUI_LINE" != *'type="Foreground"'* ]]; then
  echo "✗ GUI is not a foreground application" >&2
  exit 1
fi
if [[ -z "$DAEMON_LINE" || "$DAEMON_LINE" == *'type="Foreground"'* ]]; then
  echo "✗ daemon would create a duplicate Dock icon" >&2
  exit 1
fi
if [[ "$DAEMON_LINE" != *'type="UIElement"'* ]]; then
  echo "✗ daemon is not classified as a UIElement" >&2
  exit 1
fi

echo "✓ one foreground GUI; shared daemon is Dock-less (UIElement)"
