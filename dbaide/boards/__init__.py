"""Saved questions and dashboards — the BI 'pin once, keep watching' layer.

A :class:`SavedQuestion` is a re-runnable unit captured from one answer; a
:class:`Dashboard` is an ordered grid of tiles, each referencing a saved
question by id. Persistence mirrors the per-instance JSON stores elsewhere in
the project (atomic write, upsert by id).
"""

from dbaide.boards.models import Dashboard, SavedQuestion, Tile, new_id, utc_now
from dbaide.boards.store import DashboardStore, SavedQuestionStore

__all__ = [
    "Dashboard",
    "SavedQuestion",
    "Tile",
    "DashboardStore",
    "SavedQuestionStore",
    "new_id",
    "utc_now",
]
