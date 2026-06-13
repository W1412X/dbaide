"""Release version check helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from dbaide.release_check import (
    ReleaseCheckResult,
    ReleaseInfo,
    check_for_update,
    compare_versions,
    fetch_latest_release,
    is_update_available,
    normalize_version,
)


def test_normalize_version_strips_v_prefix():
    assert normalize_version("v0.2.2") == "0.2.2"
    assert normalize_version("V1.0.0") == "1.0.0"


def test_compare_versions():
    assert compare_versions("0.2.1", "0.2.2") == -1
    assert compare_versions("0.2.2", "0.2.2") == 0
    assert compare_versions("1.0.0", "0.9.9") == 1


def test_is_update_available():
    assert is_update_available("0.2.1", "0.2.2") is True
    assert is_update_available("0.2.2", "0.2.2") is False
    assert is_update_available("0.2.3", "0.2.2") is False


def test_is_ahead_of_release():
    from dbaide.release_check import is_ahead_of_release

    assert is_ahead_of_release("0.3.0", "0.2.2") is True
    assert is_ahead_of_release("0.2.2", "0.2.2") is False
    assert is_ahead_of_release("0.2.1", "0.2.2") is False


def test_check_for_update_when_current_is_ahead():
    latest = ReleaseInfo(tag="v0.2.2", version="0.2.2", html_url="https://example/release", name="0.2.2")
    with patch("dbaide.release_check.fetch_latest_release", return_value=latest):
        result = check_for_update(current="0.3.0-dev")
    assert result.ok is True
    assert result.update_available is False
    assert result.ahead_of_release is True


def test_fetch_latest_release_parses_github_response():
    payload = json.dumps({
        "tag_name": "v0.2.3",
        "html_url": "https://github.com/W1412X/dbaide/releases/tag/v0.2.3",
        "name": "0.2.3",
    }).encode()

    fake_resp = MagicMock()
    fake_resp.read.return_value = payload
    fake_resp.__enter__ = MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = MagicMock(return_value=False)

    with patch("dbaide.release_check.urllib.request.urlopen", return_value=fake_resp):
        info = fetch_latest_release(timeout=1.0)

    assert info == ReleaseInfo(
        tag="v0.2.3",
        version="0.2.3",
        html_url="https://github.com/W1412X/dbaide/releases/tag/v0.2.3",
        name="0.2.3",
    )


def test_check_for_update_reports_newer_release():
    latest = ReleaseInfo(tag="v9.9.9", version="9.9.9", html_url="https://example/release", name="9.9.9")
    with patch("dbaide.release_check.fetch_latest_release", return_value=latest):
        result = check_for_update(current="0.2.2")
    assert result.ok is True
    assert result.update_available is True
    assert result.latest == latest


def test_check_for_update_handles_network_error():
    with patch("dbaide.release_check.fetch_latest_release", side_effect=OSError("network down")):
        result = check_for_update(current="0.2.2")
    assert result.ok is False
    assert result.latest is None
    assert "network down" in result.error
