# SPDX-License-Identifier: GPL-3.0-or-later
"""Attrappe eines TrueNAS-Servers für die Tests.

Startet auf 127.0.0.1 einen kleinen TLS-WebSocket-Server mit einem
selbstsignierten Test-Zertifikat (wird bei jedem Testlauf neu erzeugt) und
beantwortet JSON-RPC-Aufrufe mit vorgegebenen Mock-Antworten.

Die Antwortformen orientieren sich am TrueNAS-Quellcode 25.10.7
(Fehler-Format aus api/base/server/ws_handler/rpc.py, "Not authorized"
mit errno EACCES aus main.py).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading

from truenas_widget.wsclient import OP_TEXT, WebSocket, encode_frame, _WS_GUID

TEST_KEY = "1-TESTKEYabcdefghijklmnopqrstuvwxyz0123456789SECRET"
TEST_USER = "widget-leser"


def make_cert(directory: str) -> tuple[str, str, str]:
    """Erzeugt ein selbstsigniertes Zertifikat. Gibt (cert, key, sha256-hex) zurück."""
    if not shutil.which("openssl"):
        raise RuntimeError("openssl fehlt")
    cert = os.path.join(directory, "cert.pem")
    key = os.path.join(directory, "key.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
         "-out", cert, "-days", "1", "-subj", "/CN=mock-truenas"],
        check=True, capture_output=True,
    )
    with open(cert) as fh:
        der = ssl.PEM_cert_to_DER_cert(fh.read())
    return cert, key, hashlib.sha256(der).hexdigest()


class _ServerWS(WebSocket):
    def send_text(self, text: str) -> None:
        self._sendall(encode_frame(OP_TEXT, text.encode(), mask=False))


class MockTrueNAS:
    def __init__(self, cert: str, key: str, responses: dict | None = None,
                 denied: set | None = None, api_key: str = TEST_KEY, username: str = TEST_USER):
        self.responses = responses or {}
        self.denied = denied or set()
        self.api_key = api_key
        self.username = username
        self.received_methods: list[str] = []
        self.app_bytes_received = 0
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.sock.settimeout(0.2)  # damit der Server-Thread schnell beendet werden kann
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(timeout=5)

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        conn.settimeout(5)
        try:
            tls = self.ctx.wrap_socket(conn, server_side=True)
        except (OSError, ssl.SSLError):
            conn.close()
            return
        ws = _ServerWS(tls)
        try:
            # Erst einmal lesen: kommt überhaupt etwas an?
            first = ws._recv_some()
            self.app_bytes_received += len(first)
            ws._buf = first
            header = ws._read_until(b"\r\n\r\n", 16384).decode()
            key = ""
            for line in header.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
            tls.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
            )
            # Eine Event-Nachricht ohne id vorab: der Client muss sie überspringen.
            ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "collection_update", "params": {}}))
            while True:
                req = json.loads(ws.recv_text())
                self.received_methods.append(req["method"])
                ws.send_text(json.dumps(self._answer(req)))
        except Exception:
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _answer(self, req):
        method, rid, params = req["method"], req["id"], req.get("params", [])
        if method == "auth.login_ex":
            p = params[0]
            ok = (p.get("mechanism") == "API_KEY_PLAIN" and p.get("api_key") == self.api_key
                  and p.get("username") == self.username)
            result = {"response_type": "SUCCESS", "user_info": None, "authenticator": "LEVEL_1"} \
                if ok else {"response_type": "AUTH_ERR"}
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        if method in self.denied:
            return {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32001, "message": "Method call error",
                "data": {"error": 13, "errname": "EACCES", "reason": "Not authorized",
                         "trace": None, "extra": None}}}
        if method in self.responses:
            return {"jsonrpc": "2.0", "id": rid, "result": self.responses[method]}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}


class CertDir:
    """Erzeugt ein Test-Zertifikat in einem temporären Ordner (einmal pro Testklasse)."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cert, self.key, self.fingerprint = make_cert(self.tmp.name)
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()
