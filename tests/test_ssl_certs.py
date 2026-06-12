"""HTTPS certificate trust helpers."""

from __future__ import annotations

import ssl
from unittest.mock import MagicMock, patch

import pytest

from dbaide.llm_errors import classify_llm_error
from dbaide.ssl_certs import HttpsCertCheck, check_https_certificates, https_ssl_context


def test_https_ssl_context_loads_certifi_bundle():
    ctx = https_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_check_https_certificates_ok_when_tls_succeeds():
    fake_sock = MagicMock()
    fake_tls = MagicMock()
    fake_tls.getpeercert.return_value = {"subject": ()}
    fake_tls.__enter__ = MagicMock(return_value=fake_tls)
    fake_tls.__exit__ = MagicMock(return_value=False)

    with patch("dbaide.ssl_certs.socket.create_connection", return_value=fake_sock):
        fake_sock.__enter__ = MagicMock(return_value=fake_sock)
        fake_sock.__exit__ = MagicMock(return_value=False)
        with patch("dbaide.ssl_certs.https_ssl_context") as mock_ctx:
            mock_ctx.return_value.wrap_socket.return_value = fake_tls
            result = check_https_certificates(host="example.com", port=443, timeout=1.0)
    assert result == HttpsCertCheck(True)


def test_check_https_certificates_reports_ssl_failure():
    with patch("dbaide.ssl_certs.socket.create_connection", side_effect=ssl.SSLCertVerificationError("bad cert")):
        result = check_https_certificates(host="example.com", port=443, timeout=1.0)
    assert result.ok is False
    assert "bad cert" in result.detail


def test_classify_llm_error_ssl_hint():
    err = classify_llm_error(RuntimeError("LLM connection failed: certificate verify failed"))
    assert "HTTPS" in err.hint or "证书" in err.hint
