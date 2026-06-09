"""Tests for the debug bundle export."""
from pathlib import Path

from dbaide.history.debug_bundle import create_desktop_debug_bundle


def test_desktop_bundle_sanitizes_connection_name(tmp_path: Path):
    """A connection name containing path separators must not place the zip
    outside the output directory (path traversal defence)."""
    output_dir = tmp_path / "debug"
    path = create_desktop_debug_bundle(
        config={},
        context={"connection_name": "../../etc/evil"},
        output_dir=output_dir,
    )
    # The zip must land inside output_dir, not escape via ../
    assert path.parent == output_dir
    assert "/" not in path.name
    assert path.exists()


def test_desktop_bundle_empty_connection_name(tmp_path: Path):
    """When connection_name is absent or empty, the filename should use the
    'desktop' fallback, not crash."""
    output_dir = tmp_path / "debug"
    path = create_desktop_debug_bundle(
        config={},
        context={},
        output_dir=output_dir,
    )
    assert "desktop" in path.name
    assert path.exists()
