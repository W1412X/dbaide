"""TLS/SSL connection options for remote (postgres/mysql) connections:
config validation + round-trip, and the per-dialect SSL setup."""

from __future__ import annotations

import ssl

import pytest

from dbaide.adapters.mysql import _mysql_ssl_context
from dbaide.config import ConfigManager
from dbaide.models import ConnectionConfig


# ── ConnectionConfig validation ──────────────────────────────────────────────

def test_sslmode_accepted_values():
    for mode in ("", "disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
        c = ConnectionConfig(name="c", type="postgres", host="h", sslmode=mode)
        assert c.sslmode == mode


def test_sslmode_rejects_unknown():
    with pytest.raises(ValueError):
        ConnectionConfig(name="c", type="postgres", host="h", sslmode="bogus")


def test_sslmode_lowercased_and_stripped():
    c = ConnectionConfig(name="c", type="mysql", host="h", sslmode="  VERIFY-FULL ")
    assert c.sslmode == "verify-full"


def test_ssl_ca_path_preserved():
    c = ConnectionConfig(name="c", type="postgres", host="h", sslmode="verify-ca", ssl_ca="/etc/ssl/ca.pem")
    assert c.ssl_ca == "/etc/ssl/ca.pem"


def test_defaults_empty():
    c = ConnectionConfig(name="c", type="postgres", host="h")
    assert c.sslmode == "" and c.ssl_ca == ""


# ── config.toml round-trip ───────────────────────────────────────────────────

def test_ssl_config_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    cfg = ConfigManager(path=path)
    cfg.upsert_connection(
        ConnectionConfig(name="db", type="mysql", host="h", sslmode="verify-full", ssl_ca="/tmp/ca.pem"),
        make_default=True,
    )
    loaded = ConfigManager(path=path).get_connection("db")
    assert loaded.sslmode == "verify-full"
    assert loaded.ssl_ca == "/tmp/ca.pem"


# ── MySQL SSL context builder ────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["", "disable", "allow", "prefer"])
def test_mysql_no_context_for_plaintext_modes(mode):
    assert _mysql_ssl_context(mode, "") is None


def test_mysql_require_encrypts_without_verify():
    ctx = _mysql_ssl_context("require", "")
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


def test_mysql_verify_ca_verifies_chain_not_hostname():
    ctx = _mysql_ssl_context("verify-ca", "")
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_mysql_verify_full_verifies_chain_and_hostname():
    ctx = _mysql_ssl_context("verify-full", "")
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_mysql_verify_ca_bad_cafile_raises(tmp_path):
    # A nonexistent CA path surfaces a load error rather than silently
    # downgrading to no verification.
    missing = tmp_path / "ca.pem"
    with pytest.raises(Exception):
        _mysql_ssl_context("verify-ca", str(missing))
