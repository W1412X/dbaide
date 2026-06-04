#!/usr/bin/env bash
# Ad-hoc sign a PyInstaller .app so it launches on modern macOS without a Developer ID.
# Usage: ./scripts/codesign_macos.sh dist/DBAide.app
set -euo pipefail

APP="${1:?Usage: codesign_macos.sh /path/to/App.app}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "codesign_macos.sh: macOS only" >&2
  exit 1
fi

if [[ ! -d "$APP" ]]; then
  echo "codesign_macos.sh: not found: $APP" >&2
  exit 1
fi

echo "==> Ad-hoc signing $APP"

# Inner libraries first, then executables, then the bundle.
while IFS= read -r -d '' f; do
  codesign --force --sign - "$f"
done < <(find "$APP" \( -name "*.dylib" -o -name "*.so" \) -print0 | sort -z)

while IFS= read -r -d '' f; do
  codesign --force --sign - "$f"
done < <(find "$APP" -name "*.framework" -print0 | sort -z)

while IFS= read -r -d '' f; do
  if file "$f" | grep -q "Mach-O"; then
    codesign --force --sign - "$f"
  fi
done < <(find "$APP" -type f -perm +111 -print0 | sort -z)

codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
echo "==> Signed OK: $APP"
