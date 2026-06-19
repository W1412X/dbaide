#!/usr/bin/env bash
# Refresh bundled WebEngine vendor JS (marked, highlight.js, echarts).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/dbaide/desktop/assets/vendor"
mkdir -p "$VENDOR"

fetch() {
  local url="$1" out="$2"
  echo "→ $out"
  curl -fsSL --max-time 120 "$url" -o "$out"
}

fetch "https://unpkg.com/marked@12.0.2/lib/marked.umd.js" "$VENDOR/marked.umd.js"
fetch "https://unpkg.com/@highlightjs/cdn-assets@11.9.0/highlight.min.js" "$VENDOR/highlight.min.js"
fetch "https://unpkg.com/echarts@5.6.0/dist/echarts.min.js" "$VENDOR/echarts.min.js"

ls -lh "$VENDOR"
