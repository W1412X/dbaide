"""Pure grid-packing engine tests (no Qt)."""

from __future__ import annotations

from dbaide.boards.grid import COLS, clamp_size, grid_rows, move_to_index, pack


def _t(qid, w, h):
    return {"question_id": qid, "w": w, "h": h}


def _no_overlap(packed):
    for i, a in enumerate(packed):
        for b in packed[i + 1:]:
            ax, ay, aw, ah = a["x"], a["y"], a["w"], a["h"]
            bx, by, bw, bh = b["x"], b["y"], b["w"], b["h"]
            overlap = not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)
            assert not overlap, f"{a['question_id']} overlaps {b['question_id']}"


def test_clamp_size_bounds():
    assert clamp_size(0, 0) == (3, 2)         # below minimums
    assert clamp_size(99, 4) == (COLS, 4)     # width capped at COLS
    assert clamp_size(6, 5) == (6, 5)


def test_two_half_tiles_sit_side_by_side():
    packed = pack([_t("a", 6, 5), _t("b", 6, 5)])
    a, b = packed
    assert (a["x"], a["y"]) == (0, 0)
    assert (b["x"], b["y"]) == (6, 0)         # second half-width tile fills the right
    _no_overlap(packed)


def test_full_width_tile_wraps_to_next_row():
    packed = pack([_t("a", 6, 5), _t("b", 6, 5), _t("c", 12, 4)])
    c = packed[2]
    assert c["x"] == 0 and c["y"] == 5        # full row drops below the two halves
    _no_overlap(packed)


def test_packing_is_overlap_free_for_mixed_sizes():
    tiles = [_t("a", 6, 5), _t("b", 4, 3), _t("c", 8, 6), _t("d", 12, 2),
             _t("e", 3, 4), _t("f", 6, 5), _t("g", 5, 3)]
    _no_overlap(pack(tiles))


def test_grid_rows_reports_total_height():
    packed = pack([_t("a", 6, 5), _t("b", 6, 5), _t("c", 12, 4)])
    assert grid_rows(packed) == 9             # row 0..5 (halves) + 5..9 (full)


def test_move_to_index_reorders():
    tiles = [_t("a", 6, 5), _t("b", 6, 5), _t("c", 6, 5)]
    order = [t["question_id"] for t in move_to_index(tiles, "c", 0)]
    assert order == ["c", "a", "b"]
    order = [t["question_id"] for t in move_to_index(tiles, "a", 99)]
    assert order == ["b", "c", "a"]
    # unknown id → unchanged
    assert [t["question_id"] for t in move_to_index(tiles, "zzz", 0)] == ["a", "b", "c"]


def test_pack_does_not_mutate_input():
    tiles = [_t("a", 6, 5)]
    pack(tiles)
    assert "x" not in tiles[0]
