"""The management door has to be HTTPS-only — proved, not assumed.

The defect this file guards is subtle: ``curl`` exits **zero** when it completes
an HTTP 400, so ``if curl ... http://:8443/admin`` was true on a perfectly
healthy TLS-only listener.  Worse, a listener that really did serve the page in
clear text looked exactly the same.

These tests drive the shipped shell helpers against real sockets: a server that
answers 400 (nginx's rejection), one that answers 200 (the accident), a closed
port, and a genuine TLS server.  No Pi and no mocking of the socket layer.
"""

from __future__ import annotations

import http.server
import re
import socket
import ssl
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "pi" / "lib" / "common.sh"
ADMIN_STEP = ROOT / "pi" / "steps" / "65-admin.sh"
ADMIN_CONF = ROOT / "pi" / "nginx" / "ark-admin.conf"
PLAYERS_CONF = ROOT / "pi" / "nginx" / "ark-players.conf"
VERIFY_STEP = ROOT / "pi" / "steps" / "90-verify.sh"


def _shell(script: str, *args: str) -> str:
    """Run a tiny script with common.sh sourced, exactly as the Pi does."""
    command = 'source "$1"; shift; ' + script
    done = subprocess.run(
        ["bash", "-c", command, "_", str(COMMON), *args],
        capture_output=True, text=True, timeout=20,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 400

    def do_GET(self):  # noqa: N802 - the http.server API
        body = b"plain"
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output readable
        pass


def _serve(status: int = 400, tls: ssl.SSLContext | None = None):
    handler = type("Handler", (_Handler,), {"status": status})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _certificate(tmp_path: Path) -> tuple[Path, Path]:
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(
            [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    crt = tmp_path / "probe.crt"
    pem = tmp_path / "probe.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return crt, pem


@pytest.mark.parametrize("status, verdict, safe", [
    ("400", "rejected 400", True),
    ("497", "rejected 497", True),
    ("000", "no-response", True),
    ("200", "served 200", False),
    ("302", "served 302", False),
    ("401", "served 401", False),   # clear-text sign-in is still clear text
    ("403", "served 403", False),
    ("404", "served 404", False),
    ("500", "served 500", False),
])
def test_the_plain_http_verdict_is_a_table_not_a_curl_exit_code(status, verdict, safe):
    assert _shell('ark_plain_http_verdict "$1"', status) == verdict
    check = subprocess.run(
        ["bash", "-c", 'source "$1"; ark_plain_http_is_rejected "$2"',
         "_", str(COMMON), status], capture_output=True, text=True)
    assert (check.returncode == 0) is safe, (status, check.returncode)


def test_a_completed_400_is_rejected_and_a_completed_200_is_not():
    """Both make curl exit zero; only one is a healthy TLS-only listener."""
    for status, expected in ((400, "rejected 400"), (200, "served 200")):
        server, port = _serve(status)
        try:
            code = _shell('ark_http_code "http://127.0.0.1:$1/"', str(port)).strip()
            assert code == str(status)
            assert _shell('ark_plain_http_verdict "$1"', code) == expected
        finally:
            server.shutdown()
            server.server_close()


def test_a_closed_port_reports_no_response_not_a_status():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    code = _shell('ark_http_code "http://127.0.0.1:$1/"', str(port)).strip()
    assert code == "000"
    assert _shell('ark_plain_http_verdict "$1"', code) == "no-response"


def test_a_real_tls_listener_answers_over_https_and_still_rejects_plain_http(tmp_path):
    crt, key = _certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(crt), str(key))
    server, port = _serve(403, tls=context)
    try:
        tls_code = _shell('ark_http_code "https://127.0.0.1:$1/" -k', str(port)).strip()
        assert tls_code == "403"          # expected unauthenticated answer
        plain_code = _shell('ark_http_code "http://127.0.0.1:$1/"', str(port)).strip()
        assert plain_code in ("000", "400")
        assert _shell('ark_plain_http_verdict "$1"', plain_code).startswith(
            ("rejected", "no-response"))
    finally:
        server.shutdown()
        server.server_close()


def test_the_installer_uses_the_verdict_instead_of_curl_exit_status():
    text = ADMIN_STEP.read_text(encoding="utf-8")
    assert "ark_plain_http_is_rejected" in text
    assert "ark_plain_http_verdict" in text
    # The old shape: a bare successful curl was treated as failure.
    assert "if curl -s -o /dev/null --max-time 5 http://127.0.0.1:8443" not in text
    assert "if curl -s -o /dev/null --max-time 5 http://127.0.0.1:8445" not in text
    # An unauthenticated 200 on the management origin is a failure, not liveness.
    assert "200)         ark_die" in text or "200) ark_die" in text
    # TLS liveness for both secure origins goes through the same helper.
    assert text.count("tls_code ark.lan:8443") >= 2
    assert "tls_code ark.lan:8445" in text


def test_every_tls_probe_asks_by_name_because_an_ip_literal_sends_no_sni():
    """A TLS probe to 127.0.0.1 sends no SNI, so nginx serves the default server…
    which carries `ssl_reject_handshake on` and drops the connection. curl then
    reports 000 on a healthy listener, and the phase that should prove the door
    fails instead. That is exactly what happened on the box on 25 Oct 2026:
    `https://127.0.0.1:8443/admin returned '000'` failed phase 65-admin, and five
    minutes later three checks in 90-verify reported the same '000'."""
    common = COMMON.read_text(encoding="utf-8")
    assert "ark_tls_code() { # <host:port> <path> [extra curl arguments...]" in common
    assert '--resolve "${hp}:127.0.0.1"' in common

    # No TLS probe may go straight to an address literal, in either step.
    for step in (ADMIN_STEP, VERIFY_STEP):
        text = step.read_text(encoding="utf-8")
        assert "ark_http_code https://" not in text
        assert "curl -sk" not in text
        assert "https://127.0.0.1" not in text

    # …while the plain-HTTP probes must still use the literal: they are asking
    # the port, not the name, and a name there would only add a DNS dependency.
    admin = ADMIN_STEP.read_text(encoding="utf-8")
    assert "ark_http_code http://127.0.0.1:8443/admin" in admin
    assert "ark_http_code http://127.0.0.1:8445/arcade/" in admin

    # The verifier asks the same three doors the same way the installer does.
    verify = VERIFY_STEP.read_text(encoding="utf-8")
    assert "ark_tls_code ark.lan:8443 /admin" in verify
    assert "ark_tls_code ark.lan:8443 /admin/community" in verify
    assert "ark_tls_code ark.lan:8443 /admin/arcade" in verify

    # The management listener keeps a refusing default server, and that is the
    # whole reason a nameless probe dies there.  (:8445 has no default server,
    # so it answered — which is why only the :8443 probes failed.)
    assert "ssl_reject_handshake on" in ADMIN_CONF.read_text(encoding="utf-8")
    assert "ssl_reject_handshake" not in PLAYERS_CONF.read_text(encoding="utf-8")


def test_no_shell_line_ends_with_a_doubled_quote():
    """`x="$(cmd)""` is not a syntax error bash -n can see: the second quote
    *opens* a string that runs on, and if any later line closes it the file ends
    up balanced and bash -n passes while the assignment swallows whole lines of
    code. That is how a patch of mine shipped on 25 Oct 2026 — 90-verify then
    died at runtime with `adm_code: unbound variable` on a healthy box, after
    bash -n had said the file was fine."""
    pattern = re.compile(r'\)""\s*$')
    for path in (COMMON, ADMIN_STEP, VERIFY_STEP):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not pattern.search(line), (path, number, line)


def test_every_shell_file_still_parses():
    for path in (COMMON, ADMIN_STEP, VERIFY_STEP):
        done = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert done.returncode == 0, (path, done.stderr)

