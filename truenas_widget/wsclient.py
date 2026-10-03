"""Kleiner WebSocket-Client (RFC 6455) über TLS mit Fingerabdruck-Prüfung.

Warum eine eigene, kleine Umsetzung statt einer Bibliothek?
- Es muss nichts installiert werden (nur Python-Standardbibliothek). Auf
  CachyOS/Arch darf man in das System-Python nicht einfach mit pip
  installieren; ein Paket wie "python-websockets" wäre also eine
  zusätzliche Abhängigkeit, die man pflegen muss.
- Wir brauchen nur einen winzigen Teil: Textnachrichten senden/empfangen.
- Vor allem: Wir kontrollieren den TLS-Aufbau selbst. Der Fingerabdruck
  des Server-Zertifikats wird geprüft, BEVOR auch nur ein einziges Byte
  (und damit erst recht der API-Key) an den Server geht.

Zur Zertifikatsprüfung (wichtig!):
TrueNAS benutzt hier ein selbstsigniertes Zertifikat. Die normale Prüfung
("ist das Zertifikat von einer bekannten Zertifizierungsstelle
unterschrieben und passt der Name?") würde deshalb immer scheitern.
Statt sie einfach abzuschalten, ersetzen wir sie durch eine STRENGERE
Prüfung: Der SHA-256-Fingerabdruck des Zertifikats muss exakt dem Wert
in der Konfiguration entsprechen ("Certificate Pinning"). Stimmt er
nicht, wird die Verbindung sofort getrennt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import socket
import ssl
import struct

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE_BYTES = 32 * 1024 * 1024  # Schutz vor riesigen Antworten

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class ConnectError(Exception):
    """Server nicht erreichbar (Netz, Zeitüberschreitung, TLS-Fehler)."""


class FingerprintMismatch(ConnectError):
    """Das Zertifikat des Servers hat einen anderen Fingerabdruck als erwartet."""

    def __init__(self, actual_hex: str):
        super().__init__("Zertifikats-Fingerabdruck stimmt nicht mit der Konfiguration überein.")
        self.actual_hex = actual_hex


class ProtocolError(ConnectError):
    """Die Gegenseite spricht nicht korrekt WebSocket."""


class ConnectionClosed(ConnectError):
    """Die Verbindung wurde geschlossen."""


def _tls_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    # Die Namens- und CA-Prüfung wird hier bewusst durch die
    # Fingerabdruck-Prüfung in open_tls() ERSETZT (siehe Erklärung oben).
    # Ohne passenden Fingerabdruck wird nichts gesendet.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_fingerprint(host: str, port: int, timeout: float) -> str:
    """Baut nur die TLS-Verbindung auf und gibt den SHA-256-Fingerabdruck zurück.

    Es wird dabei nichts gesendet (kein Key, keine Anfrage).
    """
    sock = open_tls(host, port, timeout, expected_fingerprint=None)
    try:
        return _peer_fingerprint(sock)
    finally:
        _close_quietly(sock)


def _peer_fingerprint(sock: ssl.SSLSocket) -> str:
    der = sock.getpeercert(binary_form=True)
    if not der:
        raise ConnectError("Server hat kein Zertifikat geschickt.")
    return hashlib.sha256(der).hexdigest()


def _close_quietly(sock) -> None:
    try:
        sock.close()
    except OSError:
        pass


def open_tls(host: str, port: int, timeout: float, expected_fingerprint: str | None) -> ssl.SSLSocket:
    """Öffnet eine TLS-Verbindung. Ist expected_fingerprint gesetzt, wird er geprüft."""
    sni = host.strip("[]")
    try:
        raw = socket.create_connection((sni, port), timeout=timeout)
    except (OSError, socket.timeout) as exc:
        raise ConnectError(f"Keine Verbindung zu {host}:{port} ({_reason(exc)}).") from None
    try:
        sock = _tls_context().wrap_socket(raw, server_hostname=sni)
    except (OSError, ssl.SSLError) as exc:
        _close_quietly(raw)
        raise ConnectError(f"TLS-Verbindung zu {host}:{port} fehlgeschlagen ({_reason(exc)}).") from None
    if expected_fingerprint is not None:
        actual = _peer_fingerprint(sock)
        # compare_digest: Vergleich in konstanter Zeit (gute Praxis).
        if not hmac.compare_digest(actual, expected_fingerprint):
            _close_quietly(sock)
            raise FingerprintMismatch(actual)
    return sock


def _reason(exc: BaseException) -> str:
    if isinstance(exc, socket.timeout):
        return "Zeitüberschreitung"
    if isinstance(exc, ConnectionRefusedError):
        return "Verbindung abgelehnt"
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return type(exc).__name__


def encode_frame(opcode: int, payload: bytes, mask: bool = True, fin: bool = True) -> bytes:
    """Baut einen WebSocket-Rahmen. Clients MÜSSEN maskieren (RFC 6455)."""
    head = bytearray()
    head.append((0x80 if fin else 0) | opcode)
    length = len(payload)
    mask_bit = 0x80 if mask else 0
    if length < 126:
        head.append(mask_bit | length)
    elif length < 65536:
        head.append(mask_bit | 126)
        head += struct.pack("!H", length)
    else:
        head.append(mask_bit | 127)
        head += struct.pack("!Q", length)
    if mask:
        key = os.urandom(4)
        head += key
        payload = _apply_mask(payload, key)
    return bytes(head) + payload


def _apply_mask(data: bytes, key: bytes) -> bytes:
    if not data:
        return b""
    # Schnelle XOR-Maskierung über grosse Ganzzahlen.
    n = len(data)
    full_key = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(full_key, "big")).to_bytes(n, "big")


class WebSocket:
    """Eine offene WebSocket-Verbindung (nur Textnachrichten)."""

    def __init__(self, sock):
        self.sock = sock
        self._buf = b""
        self.closed = False

    # ---------- Verbindungsaufbau ----------
    @classmethod
    def connect(cls, host: str, port: int, path: str, fingerprint: str, timeout: float) -> "WebSocket":
        if not fingerprint:
            raise ConnectError("Ohne Fingerabdruck wird keine Verbindung aufgebaut.")
        sock = open_tls(host, port, timeout, expected_fingerprint=fingerprint)
        ws = cls(sock)
        try:
            ws._handshake(host, port, path)
        except Exception:
            ws.close()
            raise
        return ws

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: truenas-widget\r\n"
            "\r\n"
        )
        self._sendall(request.encode())
        header = self._read_until(b"\r\n\r\n", limit=16384)
        lines = header.decode("iso-8859-1").split("\r\n")
        status = lines[0].split(" ", 2)
        if len(status) < 2 or status[1] != "101":
            code = status[1] if len(status) > 1 else "?"
            raise ProtocolError(f"WebSocket-Aufbau abgelehnt (HTTP {code}). Pfad oder API-Version falsch?")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        expected = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise ProtocolError("Ungültige WebSocket-Antwort vom Server.")

    # ---------- Senden/Empfangen ----------
    def send_text(self, text: str) -> None:
        self._sendall(encode_frame(OP_TEXT, text.encode("utf-8")))

    def recv_text(self) -> str:
        """Wartet auf die nächste vollständige Textnachricht."""
        parts: list[bytes] = []
        total = 0
        msg_opcode = None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == OP_PING:
                self._sendall(encode_frame(OP_PONG, payload))
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                self.closed = True
                try:
                    self._sendall(encode_frame(OP_CLOSE, payload[:2]))
                except ConnectError:
                    pass
                raise ConnectionClosed("Server hat die Verbindung geschlossen.")
            if opcode in (OP_TEXT, OP_BINARY):
                msg_opcode = opcode
                parts, total = [], 0
            elif opcode != OP_CONT or msg_opcode is None:
                raise ProtocolError("Unerwarteter WebSocket-Rahmen.")
            parts.append(payload)
            total += len(payload)
            if total > MAX_MESSAGE_BYTES:
                raise ProtocolError("Antwort des Servers ist zu gross.")
            if fin:
                return b"".join(parts).decode("utf-8")

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._sendall(encode_frame(OP_CLOSE, struct.pack("!H", 1000)))
            except ConnectError:
                pass
        _close_quietly(self.sock)

    # ---------- intern ----------
    def _sendall(self, data: bytes) -> None:
        try:
            self.sock.sendall(data)
        except (OSError, socket.timeout) as exc:
            raise ConnectError(f"Senden fehlgeschlagen ({_reason(exc)}).") from None

    def _recv_some(self) -> bytes:
        try:
            chunk = self.sock.recv(65536)
        except (OSError, socket.timeout) as exc:
            raise ConnectError(f"Empfangen fehlgeschlagen ({_reason(exc)}).") from None
        if not chunk:
            raise ConnectionClosed("Verbindung unerwartet beendet.")
        return chunk

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            self._buf += self._recv_some()
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _read_until(self, marker: bytes, limit: int) -> bytes:
        while marker not in self._buf:
            if len(self._buf) > limit:
                raise ProtocolError("Antwort-Kopf zu gross.")
            self._buf += self._recv_some()
        idx = self._buf.index(marker)
        data, self._buf = self._buf[:idx], self._buf[idx + len(marker):]
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        b1, b2 = self._read_exact(2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._read_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._read_exact(8))
        if length > MAX_MESSAGE_BYTES:
            raise ProtocolError("Antwort des Servers ist zu gross.")
        key = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = _apply_mask(payload, key)
        return fin, opcode, payload


def decode_frame(data: bytes) -> tuple[bool, int, bytes, int]:
    """Hilfsfunktion für Tests: zerlegt einen einzelnen Rahmen aus Bytes.

    Gibt (fin, opcode, payload, verbrauchte_bytes) zurück.
    """
    b1, b2 = data[0], data[1]
    pos = 2
    length = b2 & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", data[pos:pos + 2])
        pos += 2
    elif length == 127:
        (length,) = struct.unpack("!Q", data[pos:pos + 8])
        pos += 8
    key = b""
    if b2 & 0x80:
        key = data[pos:pos + 4]
        pos += 4
    payload = data[pos:pos + length]
    if key:
        payload = _apply_mask(payload, key)
    return bool(b1 & 0x80), b1 & 0x0F, payload, pos + length
