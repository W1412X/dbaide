#!/usr/bin/env bash
# Install Ubuntu packages required to build/test/bundle the Qt xcb platform plugin.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
mapfile -t PACKAGES < <(grep -v '^#' "$ROOT/apt-xcb-deps.txt" | grep -v '^[[:space:]]*$')

if [[ ${#PACKAGES[@]} -eq 0 ]]; then
  echo "error: no packages listed in apt-xcb-deps.txt" >&2
  exit 1
fi

sudo apt-get update -qq
sudo apt-get install -y -qq "${PACKAGES[@]}"
