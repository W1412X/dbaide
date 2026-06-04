#!/usr/bin/env bash
# Verify a .app bundle launches via Launch Services (same path as double-click).
# Usage: ./scripts/smoke_test_macos.sh dist/DBAide.app
set -euo pipefail

APP="${1:?Usage: smoke_test_macos.sh /path/to/App.app}"
WAIT="${2:-15}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "smoke_test_macos.sh: macOS only" >&2
  exit 1
fi

if [[ ! -d "$APP" ]]; then
  echo "smoke_test_macos.sh: not found: $APP" >&2
  exit 1
fi

ABS="$(cd "$(dirname "$APP")" && pwd)/$(basename "$APP")"

# Clean up any leftover process from a previous attempt.
pkill -x DBAide 2>/dev/null || true
sleep 1

echo "==> Opening $ABS (Launch Services)"
open -n "$ABS"

for _ in $(seq 1 "$WAIT"); do
  if pgrep -x DBAide >/dev/null 2>&1; then
    echo "✓ healthy (DBAide running after open)"
    pkill -x DBAide 2>/dev/null || true
    exit 0
  fi
  sleep 1
done

echo "✗ DBAide did not stay running after open"
exit 1
