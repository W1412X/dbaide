"""Application metadata and project links (settings About page, debug bundles, …)."""

from __future__ import annotations

from dbaide import __version__

APP_NAME = "DBAide"
GITHUB_OWNER = "W1412X"
GITHUB_REPO = "dbaide"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
DEVELOPER_NAME = "W1412X"
DEVELOPER_URL = f"https://github.com/{GITHUB_OWNER}"
LICENSE_NAME = "MIT"


def app_version() -> str:
    return str(__version__)


def project_links() -> list[tuple[str, str]]:
    """(i18n key, URL) pairs for the settings About page."""
    base = GITHUB_REPO_URL
    return [
        ("settings.about.link.github", base),
        ("settings.about.link.releases", f"{base}/releases"),
        ("settings.about.link.issues", f"{base}/issues"),
        ("settings.about.link.readme", f"{base}#readme"),
    ]
