"""Pure grid packing for dashboard tiles (no Qt — unit-testable).

Tiles are packed into a ``COLS``-wide grid in list order using a skyline
bottom-left heuristic: each tile drops into the lowest position where its width
fits. The layout is fully determined by ``(order, w, h)`` — dragging reorders the
list, resizing changes a tile's ``w``/``h``, and the packed result is always
overlap-free. Keeping this logic pure means the geometry can be tested without
driving real mouse events.
"""

from __future__ import annotations

from typing import Any

COLS = 12
MIN_W = 3
MIN_H = 2


def clamp_size(w: int, h: int, *, cols: int = COLS) -> tuple[int, int]:
    w = max(MIN_W, min(int(w or MIN_W), cols))
    h = max(MIN_H, int(h or MIN_H))
    return w, h


def pack(tiles: list[dict[str, Any]], *, cols: int = COLS) -> list[dict[str, Any]]:
    """Assign x/y to each tile (in list order) via skyline bottom-left packing.

    Returns new tile dicts with x, y, w, h set; never mutates the input. The
    output is overlap-free for any input order and any per-tile w/h."""
    heights = [0] * cols
    out: list[dict[str, Any]] = []
    for tile in tiles:
        w, h = clamp_size(tile.get("w"), tile.get("h"), cols=cols)
        best_x, best_y = 0, None
        for x in range(0, cols - w + 1):
            y = max(heights[x:x + w])
            if best_y is None or y < best_y:
                best_y, best_x = y, x
        best_y = best_y or 0
        for c in range(best_x, best_x + w):
            heights[c] = best_y + h
        out.append({**tile, "x": best_x, "y": best_y, "w": w, "h": h})
    return out


def grid_rows(tiles: list[dict[str, Any]]) -> int:
    """Total occupied rows for a packed layout (for sizing the scroll area)."""
    return max((int(t.get("y", 0)) + int(t.get("h", 0)) for t in tiles), default=0)


def move_to_index(tiles: list[dict[str, Any]], moved_id: str, target_index: int) -> list[dict[str, Any]]:
    """Return a new order with ``moved_id`` repositioned to ``target_index``.

    ``target_index`` is the desired slot in the *resulting* list (clamped)."""
    rest = [t for t in tiles if str(t.get("question_id") or "") != str(moved_id)]
    moved = next((t for t in tiles if str(t.get("question_id") or "") == str(moved_id)), None)
    if moved is None:
        return list(tiles)
    idx = max(0, min(int(target_index), len(rest)))
    return rest[:idx] + [moved] + rest[idx:]
