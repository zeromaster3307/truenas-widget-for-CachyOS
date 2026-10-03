"""Der Prüfer: fragt TrueNAS ab und schreibt status.json.

Wird vom systemd-User-Timer regelmässig gestartet (Standard: alle 15 Minuten)
und läuft jeweils einmal durch:

  1. Konfiguration und API-Key lesen.
  2. Verbindung aufbauen (nur mit passendem Zertifikats-Fingerabdruck).
  3. Anmelden und drei LESENDE Abfragen machen:
       alert.list     -> Warnungen/Alarme
       app.query      -> installierte Apps und ob Updates verfügbar sind
       update.status  -> ob ein Systemupdate verfügbar ist
  4. Gesamtstatus bestimmen: ok | updates | warning | critical | offline
  5. Ergebnis nach ~/.cache/truenas-widget/status.json schreiben (liest das Widget).
  6. Bei NEUEN Ereignissen eine Desktop-Benachrichtigung schicken.

Nicht erreichbar (anderes Netz, TrueNAS aus, falscher Fingerabdruck)
-> Status "offline", KEIN Alarm, KEINE Benachrichtigung.

Manuell starten:  python3 -m truenas_widget.checker
"""

from __future__ import annotations

import html
import logging
import re
import sys
import time
from datetime import datetime

from . import config as config_mod
from . import keystore, notify, paths
from .rpc import AuthFailed, Client, MethodNotAllowed, RPCError
from .wsclient import ConnectError, FingerprintMismatch

log = logging.getLogger("truenas_widget")

STATUS_SCHEMA = 1

# Rangfolge der Gesamtstatus (höher = wichtiger)
_RANK = {"ok": 0, "updates": 1, "warning": 2, "critical": 3}

# Alert-Stufen laut TrueNAS-Quellcode 25.10 (api/v25_10_0/alert.py, AlertLevel):
#   INFO, NOTICE, WARNING, ERROR, CRITICAL, ALERT, EMERGENCY
IGNORED_LEVELS = {"INFO", "NOTICE"}            # reine Info-Meldungen: ignorieren
WARNING_LEVELS = {"WARNING"}                   # -> Gesamtstatus "warning"
CRITICAL_LEVELS = {"ERROR", "CRITICAL", "ALERT", "EMERGENCY"}  # -> "critical"
# Unbekannte Stufen werden vorsichtshalber wie WARNING behandelt.

# Felder, die wir von app.query anfordern ("select" ist eine offizielle
# Query-Option, belegt in api/v25_10_0/common.py). So kommen keine
# App-Konfigurationen (mit evtl. Passwörtern) über die Leitung.
APP_FIELDS = ["name", "state", "version", "human_version", "latest_version",
              "upgrade_available", "image_updates_available", "custom_app"]

STATUS_TEXT = {
    "ok": "OK",
    "updates": "Updates verfügbar",
    "warning": "Warnung",
    "critical": "Kritisch",
    "offline": "Offline",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(value, limit: int = 300) -> str:
    """Macht aus dem Alert-Text einfachen Text (HTML-Tags entfernen, kürzen)."""
    text = html.unescape(_TAG_RE.sub(" ", str(value or "")))
    text = _WS_RE.sub(" ", text).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ------------------------------------------------------------------------
# Auswertung (reine Funktionen, gut testbar)
# ------------------------------------------------------------------------

def evaluate_alerts(alerts) -> list[dict]:
    """Nur NICHT quittierte Alerts ab Stufe WARNING."""
    result = []
    for a in alerts or []:
        if not isinstance(a, dict):
            continue
        if a.get("dismissed"):  # quittiert ("dismissed") -> ignorieren
            continue
        level = str(a.get("level") or "").upper()
        if level in IGNORED_LEVELS:
            continue
        severity = "critical" if level in CRITICAL_LEVELS else "warning"
        # "formatted" ist der fertige Text; "text" ist oft nur eine Vorlage
        # mit Platzhaltern (belegt in middlewared/alert/base.py).
        text = clean_text(a.get("formatted") or a.get("text") or "(ohne Text)")
        alert_id = str(a.get("uuid") or a.get("id") or f"{a.get('klass')}:{text}")
        result.append({"id": alert_id, "level": level or "?", "severity": severity, "text": text})
    # Kritische zuerst
    result.sort(key=lambda x: 0 if x["severity"] == "critical" else 1)
    return result


def evaluate_apps(apps) -> list[dict]:
    """Apps mit verfügbarem Update (Name, aktuelle Version, neue Version)."""
    result = []
    for app in apps or []:
        if not isinstance(app, dict):
            continue
        if not (app.get("upgrade_available") or app.get("image_updates_available")):
            continue
        new = app.get("latest_version") if app.get("upgrade_available") else None
        result.append({
            "name": str(app.get("name") or "?"),
            "current": str(app.get("version") or "?"),
            "new": str(new) if new else None,  # None = nur neues Container-Image
            "human_version": str(app.get("human_version") or ""),
        })
    result.sort(key=lambda x: x["name"].lower())
    return result


def evaluate_system_update(upd) -> tuple[dict, str | None]:
    """Liest update.status. Gibt (system_update, Problemtext oder None) zurück."""
    info = {"available": False, "new_version": None}
    if not isinstance(upd, dict):
        return info, "Systemupdate-Status unlesbar."
    if upd.get("code") == "ERROR":
        err = upd.get("error") or {}
        reason = clean_text(err.get("reason") or err.get("errname") or "unbekannt", 150)
        return info, f"Update-Prüfung auf dem TrueNAS meldet einen Fehler: {reason}"
    new_version = ((upd.get("status") or {}).get("new_version") or {})
    if isinstance(new_version, dict) and new_version.get("version"):
        info = {"available": True, "new_version": str(new_version["version"])}
    return info, None


def overall_status(alerts, app_updates, system_update) -> str:
    status = "ok"
    if app_updates or system_update.get("available"):
        status = "updates"
    for a in alerts:
        if _RANK[a["severity"]] > _RANK[status]:
            status = a["severity"]
    return status


def base_status(cfg, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    return {
        "schema": STATUS_SCHEMA,
        "system_name": cfg.name if cfg else "TrueNAS",
        "web_url": cfg.web_url if cfg else None,
        "checked_at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "checked_at_epoch": int(now),
        # Für das Widget: ab wann status.json als "nicht mehr frisch" gilt.
        "interval_minutes": cfg.interval_minutes if cfg else None,
        "status": "offline",
        "status_text": STATUS_TEXT["offline"],
        "offline_reason": None,
        "app_updates": [],
        "system_update": {"available": False, "new_version": None},
        "alerts": [],
        "problems": [],
        "incomplete": [],
    }


def offline_status(cfg, reason: str, now: float | None = None) -> dict:
    st = base_status(cfg, now)
    st["offline_reason"] = reason
    return st


def collect(client: Client) -> dict:
    """Führt die drei Abfragen aus. Fehlende Rechte werden pro Abfrage vermerkt."""
    raw = {}
    denied = []
    for key, method, params in (
        ("alerts", "alert.list", []),
        ("apps", "app.query", [[], {"select": APP_FIELDS}]),
        ("system", "update.status", []),
    ):
        try:
            raw[key] = client.call(method, params)
        except RPCError as exc:
            if exc.access_denied:
                denied.append((key, method))
                raw[key] = None
            else:
                raise
    raw["denied"] = denied
    return raw


def build_status(cfg, raw: dict, now: float | None = None) -> dict:
    st = base_status(cfg, now)
    alerts = evaluate_alerts(raw.get("alerts"))
    apps = evaluate_apps(raw.get("apps"))
    system_update, sys_problem = evaluate_system_update(raw.get("system")) \
        if raw.get("system") is not None else ({"available": False, "new_version": None}, None)
    st["alerts"], st["app_updates"], st["system_update"] = alerts, apps, system_update
    if sys_problem:
        st["problems"].append(sys_problem)
    for key, method in raw.get("denied", []):
        st["incomplete"].append(key)
        st["problems"].append(
            f"Zugriff verweigert für {method} - dem TrueNAS-Benutzer fehlt eine Leserolle."
        )
    status = overall_status(alerts, apps, system_update)
    if status == "ok" and st["incomplete"]:
        # Ohne vollständige Daten können wir "alles in Ordnung" nicht bestätigen.
        status = "offline"
        st["offline_reason"] = "Daten unvollständig (Zugriff verweigert). Diagnose ausführen."
    st["status"] = status
    st["status_text"] = STATUS_TEXT[status]
    return st


def run_once(cfg, secret, connect=Client.connect, now: float | None = None) -> dict:
    """Eine komplette Prüfung. Gibt immer einen Status zurück (wirft nicht)."""
    try:
        with connect(cfg) as client:
            client.login(cfg.username, secret)
            raw = collect(client)
        return build_status(cfg, raw, now)
    except FingerprintMismatch:
        log.warning("Zertifikats-Fingerabdruck stimmt nicht überein - Verbindung abgebrochen, Key NICHT gesendet.")
        return offline_status(
            cfg,
            "Zertifikats-Fingerabdruck stimmt nicht überein. Entweder wurde das Zertifikat "
            "auf dem TrueNAS erneuert (dann neuen Fingerabdruck prüfen und eintragen) oder "
            "jemand gibt sich als TrueNAS aus. Diagnose ausführen.", now)
    except AuthFailed as exc:
        log.warning(secret.redact(str(exc)))
        return offline_status(cfg, secret.redact(str(exc)), now)
    except MethodNotAllowed as exc:  # Programmierfehler - sollte nie passieren
        log.error(str(exc))
        return offline_status(cfg, "Interner Fehler: verbotene Methode verweigert.", now)
    except RPCError as exc:
        msg = secret.redact(str(exc))
        log.warning("TrueNAS-Fehler: %s", msg)
        return offline_status(cfg, f"TrueNAS hat mit einem Fehler geantwortet: {msg}", now)
    except ConnectError as exc:
        log.info("Nicht erreichbar: %s", secret.redact(str(exc)))
        return offline_status(cfg, f"Nicht erreichbar: {secret.redact(str(exc))}", now)
    except Exception as exc:  # unerwartet: trotzdem nur "offline", nie abstürzen
        log.error("Unerwarteter Fehler (%s)", type(exc).__name__)
        return offline_status(cfg, f"Unerwarteter Fehler ({type(exc).__name__}).", now)


def write_status(status: dict, secret=None) -> None:
    public = dict(status)
    if secret is not None:
        # Letzte Sicherung: falls der Key irgendwo im Text gelandet wäre.
        import json
        text = secret.redact(json.dumps(public, ensure_ascii=False))
        public = json.loads(text)
    notify.write_json_atomic(paths.status_file(), public)


class _RedactFilter(logging.Filter):
    """Ersetzt den Key in jeder Log-Zeile durch *** (zusätzliche Sicherung)."""

    def __init__(self, secret):
        super().__init__()
        self.secret = secret

    def filter(self, record):
        record.msg = self.secret.redact(record.getMessage())
        record.args = ()
        return True


def setup_logging() -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    return handler


def main(argv=None) -> int:
    handler = setup_logging()
    try:
        cfg = config_mod.load()
    except config_mod.ConfigError as exc:
        log.error("Konfiguration: %s", exc)
        write_status(offline_status(None, f"Konfigurationsfehler: {exc}"))
        return 2
    try:
        secret = keystore.load_key(cfg)
    except keystore.KeyError_ as exc:
        log.error("API-Key: %s", exc)
        write_status(offline_status(cfg, f"API-Key nicht lesbar: {exc}"))
        return 2
    handler.addFilter(_RedactFilter(secret))

    status = run_once(cfg, secret)
    write_status(status, secret)
    if status["status"] == "offline":
        log.info("Status: offline (%s)", status.get("offline_reason"))
    else:
        log.info("Status: %s (%d Alerts, %d App-Updates, Systemupdate: %s)",
                 status["status"], len(status["alerts"]), len(status["app_updates"]),
                 "ja" if status["system_update"]["available"] else "nein")
    notify.process(status, paths.notified_file(), cfg.notifications)
    return 0


if __name__ == "__main__":
    sys.exit(main())
