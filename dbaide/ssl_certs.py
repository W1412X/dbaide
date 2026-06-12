"""HTTPS certificate trust for LLM and other outbound TLS calls."""
from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass

import certifi

# Well-known host used only to verify that the local CA bundle can complete TLS.
_DEFAULT_PROBE_HOST = "api.openai.com"
_DEFAULT_PROBE_PORT = 443


def https_ssl_context() -> ssl.SSLContext:
    """SSL context that trusts the Mozilla CA bundle shipped with certifi."""
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=certifi.where())
    return ctx


@dataclass(frozen=True, slots=True)
class HttpsCertCheck:
    ok: bool
    detail: str = ""


def check_https_certificates(
    *,
    host: str = _DEFAULT_PROBE_HOST,
    port: int = _DEFAULT_PROBE_PORT,
    timeout: float = 5.0,
) -> HttpsCertCheck:
    """Probe TLS to *host* using the same CA bundle as LLM HTTP clients."""
    try:
        ctx = https_ssl_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                if not tls.getpeercert():
                    return HttpsCertCheck(False, "no peer certificate")
        return HttpsCertCheck(True)
    except ssl.SSLCertVerificationError as exc:
        return HttpsCertCheck(False, str(exc))
    except OSError as exc:
        return HttpsCertCheck(False, str(exc))
