#!/usr/bin/env bash
# Copy xcb / xkb runtime .so files into a PyInstaller Linux bundle.
# Qt 6.5+ requires libxcb-cursor at xcb plugin load time; CI runners have it
# installed but end-user Ubuntu machines often do not.
set -euo pipefail

BUNDLE="${1:?usage: bundle_qt_runtime_libs.sh dist/DBAide}"
INTERNAL="$BUNDLE/_internal"
if [[ ! -d "$INTERNAL" ]]; then
  INTERNAL="$BUNDLE"
fi
LIBDIR="$INTERNAL/lib"
mkdir -p "$LIBDIR"

resolve_soname() {
  local soname="$1"
  local path=""
  path="$(ldconfig -p 2>/dev/null | awk -v s="$soname" '$1 == s { print $NF; exit }' || true)"
  if [[ -z "$path" ]]; then
    for dir in /usr/lib/x86_64-linux-gnu /usr/lib64 /usr/lib; do
      if [[ -f "$dir/$soname" ]]; then
        path="$dir/$soname"
        break
      fi
    done
  fi
  printf '%s' "$path"
}

copy_soname() {
  local soname="$1"
  local path
  path="$(resolve_soname "$soname")"
  if [[ -z "$path" || ! -f "$path" ]]; then
    echo "warn: $soname not found on build host — install libxcb-cursor0 etc." >&2
    return 0
  fi
  cp -Lf "$path" "$LIBDIR/"
  echo "bundled $soname"
}

# Explicit list matches Ubuntu packages used in CI / README.
for soname in \
  libxcb-cursor.so.0 \
  libxcb-icccm.so.4 \
  libxcb-image.so.0 \
  libxcb-keysyms.so.1 \
  libxcb-randr.so.0 \
  libxcb-render-util.so.0 \
  libxcb-shape.so.0 \
  libxcb-shm.so.0 \
  libxcb-sync.so.1 \
  libxcb-xfixes.so.0 \
  libxcb-xinerama.so.0 \
  libxcb-xkb.so.1 \
  libxkbcommon-x11.so.0 \
  libxkbcommon.so.0; do
  copy_soname "$soname"
done

XCB="$(find "$INTERNAL" -path '*/platforms/libqxcb.so' -print -quit || true)"
if [[ -n "$XCB" ]]; then
  while read -r libpath; do
    [[ -n "$libpath" && -f "$libpath" ]] || continue
    case "$libpath" in
      */libxcb-*|*/libxkbcommon*) cp -Lf "$libpath" "$LIBDIR/" ;;
    esac
  done < <(ldd "$XCB" | awk '/=> \// { print $3 }')
fi

if [[ ! -f "$LIBDIR/libxcb-cursor.so.0" ]]; then
  echo "error: libxcb-cursor.so.0 was not bundled — install libxcb-cursor0 on the build host" >&2
  exit 1
fi

echo "Qt runtime libs → $LIBDIR"
