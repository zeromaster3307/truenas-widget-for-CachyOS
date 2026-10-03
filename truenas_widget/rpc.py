"""JSON-RPC-2.0-Client für TrueNAS mit FESTER Whitelist.

Es dürfen ausschliesslich die Methoden in ALLOWED_METHODS aufgerufen
werden. Jeder andere Aufruf wird verweigert, BEVOR etwas gesendet wird.
Damit kann dieses Programm auch durch einen Programmierfehler nichts auf
dem TrueNAS verändern (keine Upgrades, Neustarts, Starts, Stopps ...).
Zusätzlich hat der Benutzer auf dem TrueNAS nur eine schreibgeschützte
Rolle - das ist die zweite Sicherung.

Alle Methodennamen sind im TrueNAS-Quellcode Version 25.10.7 belegt
(github.com/truenas/middleware, Tag TS-25.10.7):
  auth.login_ex  -> plugins/auth.py, api/v25_10_0/auth.py (Mechanismus API_KEY_PLAIN)
  alert.list     -> plugins/alert.py, Rolle ALERT_LIST_READ
  app.query      -> plugins/apps/crud.py, Rolle APPS_READ
  update.status  -> plugins/update_/status.py, Rolle SYSTEM_UPDATE_READ
Die Rolle "Read-only Admin" (READONLY_ADMIN) enthält alle *_READ-Rollen
(middlewared/role.py), reicht also für alle drei Abfragen.
"""

from __future__ import annotations

import itertools
import json

from .wsclient import ConnectError, WebSocket

# --- DIE WHITELIST -------------------------------------------------------
# auth.login_ex ist keine Abfrage, aber für die Anmeldung zwingend nötig.
# Es ändert nichts am System. Alles andere sind reine Lese-Methoden.
ALLOWED_METHODS = frozenset({
    "auth.login_ex",
    "alert.list",
    "app.query",
    "update.status",
})
# --------------------------------------------------------------------------


class MethodNotAllowed(Exception):
    """Ein Aufruf ausserhalb der Whitelist wurde verweigert (nichts gesendet)."""


class RPCError(Exception):
    """TrueNAS hat mit einem Fehler geantwortet."""

    def __init__(self, method: str, code, message: str, errname: str | None, reason: str | None):
        self.method = method
        self.code = code
        self.errname = errname
        self.reason = reason
        text = f"{method}: {message}"
        if errname or reason:
            text += f" ({errname or ''}{': ' if errname and reason else ''}{reason or ''})"
        super().__init__(text)

    @property
    def access_denied(self) -> bool:
        # Belegt in middlewared/main.py: fehlende Rolle -> CallError('Not authorized', errno.EACCES)
        return self.errname == "EACCES" or (self.reason or "").strip() == "Not authorized"

    @property
    def not_authenticated(self) -> bool:
        return self.errname == "ENOTAUTHENTICATED"


class AuthFailed(Exception):
    """Anmeldung mit dem API-Key hat nicht geklappt."""

    def __init__(self, response_type: str):
        self.response_type = response_type
        hints = {
            "AUTH_ERR": "Benutzername oder API-Key falsch (oder Key widerrufen).",
            "EXPIRED": "Der API-Key ist abgelaufen oder wurde widerrufen.",
            "OTP_REQUIRED": "Für den Benutzer ist Zwei-Faktor-Anmeldung aktiv; das wird nicht unterstützt.",
            "REDIRECT": "TrueNAS verlangt eine Weiterleitung (z. B. HA-System); nicht unterstützt.",
        }
        super().__init__(
            f"Anmeldung fehlgeschlagen ({response_type}): "
            + hints.get(response_type, "Unbekannte Antwort.")
        )


def check_allowed(method: str) -> None:
    if method not in ALLOWED_METHODS:
        raise MethodNotAllowed(
            f"Methode '{method}' ist nicht auf der Whitelist und wird verweigert. "
            "Dieses Programm darf nur lesen."
        )


class Client:
    """Verbindung zu TrueNAS. Benutzung:

        with Client.connect(cfg) as c:
            c.login(cfg.username, secret)
            alerts = c.call("alert.list")
    """

    def __init__(self, transport):
        # transport braucht send_text(str), recv_text() -> str, close()
        self.transport = transport
        self._ids = itertools.count(1)

    @classmethod
    def connect(cls, cfg) -> "Client":
        ws = WebSocket.connect(cfg.host, cfg.port, cfg.ws_path, cfg.fingerprint, cfg.timeout_seconds)
        return cls(ws)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception:
            pass

    def call(self, method: str, params: list | None = None):
        """Ruft eine Methode auf. Methoden ausserhalb der Whitelist -> MethodNotAllowed."""
        check_allowed(method)  # ZUERST prüfen, dann erst senden
        req_id = next(self._ids)
        message = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or []}
        self.transport.send_text(json.dumps(message))
        while True:
            raw = self.transport.recv_text()
            try:
                reply = json.loads(raw)
            except ValueError:
                raise ConnectError("Ungültige Antwort (kein JSON) vom Server.") from None
            # Benachrichtigungen ohne passende id (z. B. Events) überspringen.
            if not isinstance(reply, dict) or reply.get("id") != req_id:
                continue
            if "error" in reply and reply["error"] is not None:
                err = reply["error"] or {}
                data = err.get("data") if isinstance(err.get("data"), dict) else {}
                raise RPCError(
                    method, err.get("code"), str(err.get("message", "Fehler")),
                    data.get("errname"), _short(data.get("reason")),
                )
            return reply.get("result")

    def login(self, username: str, secret) -> None:
        """Meldet sich mit dem API-Key an (Mechanismus API_KEY_PLAIN).

        Der Key wird nur hier ausgepackt und direkt gesendet; er wird nicht
        gespeichert und nicht protokolliert. Fehlermeldungen enthalten ihn nie.
        """
        params = [{
            "mechanism": "API_KEY_PLAIN",
            "username": username,
            "api_key": secret.reveal(),
        }]
        try:
            result = self.call("auth.login_ex", params)
        except RPCError as exc:
            # Sicherheitshalber: Text der Fehlermeldung vom Key bereinigen.
            raise RPCError("auth.login_ex", exc.code, secret.redact(str(exc)),
                           exc.errname, secret.redact(exc.reason or "")) from None
        finally:
            params[0]["api_key"] = "***"
        rtype = (result or {}).get("response_type") if isinstance(result, dict) else None
        if rtype != "SUCCESS":
            raise AuthFailed(str(rtype))


def _short(text, limit: int = 300):
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else text[:limit] + " ..."
