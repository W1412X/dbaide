"""Check GitHub Releases for a newer DBAide version."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from dbaide.app_info import APP_NAME, GITHUB_OWNER, GITHUB_REPO, app_version
from dbaide.ssl_certs import https_ssl_context

_GITHUB_LATEST = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
_VERSION_RE = re.compile(r"^\s*v?(?P<ver>\d+(?:\.\d+)*)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    tag: str
    version: str
    html_url: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class ReleaseCheckResult:
    ok: bool
    latest: ReleaseInfo | None = None
    update_available: bool = False
    ahead_of_release: bool = False
    error: str = ""


def normalize_version(version: str) -> str:
    text = str(version or "").strip()
    match = _VERSION_RE.match(text)
    return match.group("ver") if match else text.lstrip("vV")


_PRE_RELEASE_RE = re.compile(r"(\d+)(alpha|beta|rc|dev)(\d*)", re.IGNORECASE)


def version_tuple(version: str) -> tuple[int, ...]:
    normalized = normalize_version(version)
    parts: list[int] = []
    is_pre = False
    for piece in normalized.split("."):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            m = _PRE_RELEASE_RE.match(piece)
            if m:
                parts.append(int(m.group(1)))
                is_pre = True
            else:
                digits = "".join(ch for ch in piece if ch.isdigit())
                if digits:
                    parts.append(int(digits))
                    is_pre = True
            break
    result = tuple(parts or (0,))
    if is_pre:
        result = result + (-1,)
    return result


def compare_versions(left: str, right: str) -> int:
    """Return -1 if left < right, 0 if equal, 1 if left > right."""
    a = version_tuple(left)
    b = version_tuple(right)
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def is_update_available(current: str, latest: str) -> bool:
    return compare_versions(current, latest) < 0


def is_ahead_of_release(current: str, latest: str) -> bool:
    return compare_versions(current, latest) > 0


def fetch_latest_release(*, timeout: float = 8.0) -> ReleaseInfo:
    req = urllib.request.Request(
        _GITHUB_LATEST,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{APP_NAME}/{app_version()}",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=https_ssl_context()) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise RuntimeError("GitHub release response missing tag_name")
    html_url = str(data.get("html_url") or "").strip()
    if not html_url:
        html_url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    return ReleaseInfo(
        tag=tag,
        version=normalize_version(tag),
        html_url=html_url,
        name=str(data.get("name") or tag).strip(),
    )


def check_for_update(*, current: str | None = None, timeout: float = 8.0) -> ReleaseCheckResult:
    current_ver = normalize_version(current or app_version())
    try:
        latest = fetch_latest_release(timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        return ReleaseCheckResult(ok=False, error=f"HTTP {exc.code}: {body}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return ReleaseCheckResult(ok=False, error=str(exc))
    return ReleaseCheckResult(
        ok=True,
        latest=latest,
        update_available=is_update_available(current_ver, latest.version),
        ahead_of_release=is_ahead_of_release(current_ver, latest.version),
    )
