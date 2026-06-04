#!/usr/bin/env bash
# Build DBAide distributable on macOS or Linux.
# Usage:
#   ./scripts/build_package.sh gui     # PyInstaller folder → dist/DBAide/
#   ./scripts/build_package.sh cli     # single-file CLI → dist/dbaide
#   ./scripts/build_package.sh wheel   # Python wheel + sdist → dist/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TARGET="${1:-gui}"

echo "==> DBAide packaging ($TARGET) on $(uname -s)"

python3 -m pip install -q -e ".[gui,dev]"

mkdir -p dist

case "$TARGET" in
  gui)
    python3 -m PyInstaller packaging/pyinstaller/dbaide-gui.spec --noconfirm --clean
    if [[ "$(uname -s)" == "Darwin" && -d dist/DBAide.app ]]; then
      chmod +x scripts/codesign_macos.sh
      ./scripts/codesign_macos.sh dist/DBAide.app
    fi
    echo ""
    echo "GUI bundle: $ROOT/dist/DBAide/"
    if [[ -d dist/DBAide.app ]]; then
      echo "Run: open dist/DBAide.app"
    else
      echo "Run: dist/DBAide/DBAide"
    fi
    if [[ "$(uname -s)" == "Darwin" ]]; then
      echo "Optional DMG:"
      if [[ -d dist/DBAide.app ]]; then
        echo "  hdiutil create -volname DBAide -srcfolder dist/DBAide.app -ov -format UDZO dist/DBAide-macOS.dmg"
      else
        echo "  hdiutil create -volname DBAide -srcfolder dist/DBAide -ov -format UDZO dist/DBAide-macOS.dmg"
      fi
    else
      echo "Optional archive:"
      echo "  (cd dist && tar -czf DBAide-linux-$(uname -m).tar.gz DBAide)"
    fi
    ;;
  cli)
    python3 -m PyInstaller packaging/pyinstaller/dbaide-cli.spec --noconfirm --clean
    echo ""
    echo "CLI binary: $ROOT/dist/dbaide"
    ;;
  wheel)
    python3 -m pip install -q build
    python3 -m build --outdir dist
    echo ""
    echo "Wheel/sdist in $ROOT/dist/"
    echo "Install: pip install dist/dbaide-*.whl"
    ;;
  *)
    echo "Unknown target: $TARGET (use gui | cli | wheel)" >&2
    exit 1
    ;;
esac
