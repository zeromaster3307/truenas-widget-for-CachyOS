# SPDX-License-Identifier: GPL-3.0-or-later
"""Der Prüfer: fragt alle eingerichteten TrueNAS-Systeme ab und schreibt status.json.

Wird vom systemd-User-Timer regelmässig gestartet (Standard: alle 15 Minuten)
und läuft jeweils einmal durch:

  1. Konfiguration lesen (config.toml + systems/*.toml) und je System den API-Key.
  2. Alle Systeme GLEICHZEITIG prüfen (ein langsames Remote-System hält die
     anderen nicht auf). Je System:
       - Verbindung aufbauen (nur mit passendem Zertifikats-Fingerabdruck),
       - anmelden und drei LESENDE Abfragen machen:
           alert.list     -> Warnungen/Alarme
           app.query      -> installierte Apps und ob Updates verfügbar sind
           update.status  -> ob ein Systemupdate verfügbar ist
       - Status bestimmen: ok | updates | warning | critical | offline
  3. Gesamtstatus bestimmen (siehe aggregate()).
  4. Ergebnis nach ~/.cache/truenas-widget/status.json schreiben (liest das Widget).
  5. Bei NEUEN Ereignissen eine Desktop-Benachrichtigung schicken.

Nicht erreichbar -> dieses System ist "offline", KEINE Benachrichtigung.
Ein einzelnes System, das offline ist, bleibt grau. Bei mehreren Systemen
wird ein System, das 2 Prüfungen in Folge fehlt, während ein anderes
antwortet, als Warnung gezählt (im Widget pro System abschaltbar).

Manuell starten:  python3 -m truenas_widget.checker
"""

from __future__ import annotations

import html
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config as config_mod
from . import keystore, notify, paths
from .rpc import AuthFailed, Client, MethodNotAllowed, RPCError
from .wsclient import ConnectError, FingerprintMismatch

log = logging.getLogger("truenas_widget")

STATUS_SCHEMA = 2

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


def system_entry(cfg) -> dict:
    """Leerer Eintrag für ein System in status.json (Standard: offline)."""
    return {
        "id": cfg.id,
        "name": cfg.name,
        "web_url": cfg.web_url,
        "status": "offline",
        "status_text": STATUS_TEXT["offline"],
        # Warum offline? unreachable | fingerprint | auth | key | config | incomplete | error
        "offline_kind": None,
        "offline_reason": None,
        # Wie viele Prüfungen in Folge das System schon offline war (inkl. dieser)
        "offline_count": 0,
        "app_updates": [],
        "system_update": {"available": False, "new_version": None},
        "alerts": [],
        "problems": [],
        "incomplete": [],
        # Hinweise, die auch bei "OK" angezeigt werden (z. B. Zertifikat läuft bald ab).
        # Sie ändern die Farbe NICHT.
        "notices": [],
        "cert_expires": None,      # Datum JJJJ-MM-TT, bis wann das Zertifikat gilt
        "cert_days_left": None,
    }


def offline_entry(cfg, kind: str, reason: str) -> dict:
    entry = system_entry(cfg)
    entry["offline_kind"] = kind
    entry["offline_reason"] = reason
    return entry


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


def build_entry(cfg, raw: dict) -> dict:
    entry = system_entry(cfg)
    alerts = evaluate_alerts(raw.get("alerts"))
    apps = evaluate_apps(raw.get("apps"))
    system_update, sys_problem = evaluate_system_update(raw.get("system")) \
        if raw.get("system") is not None else ({"available": False, "new_version": None}, None)
    entry["alerts"], entry["app_updates"], entry["system_update"] = alerts, apps, system_update
    if sys_problem:
        entry["problems"].append(sys_problem)
    for key, method in raw.get("denied", []):
        entry["incomplete"].append(key)
        entry["problems"].append(
            f"Zugriff verweigert für {method} - dem TrueNAS-Benutzer fehlt eine Leserolle."
        )
    status = overall_status(alerts, apps, system_update)
    if status == "ok" and entry["incomplete"]:
        # Ohne vollständige Daten können wir "alles in Ordnung" nicht bestätigen.
        status = "offline"
        entry["offline_kind"] = "incomplete"
        entry["offline_reason"] = "Daten unvollständig (Zugriff verweigert). Diagnose ausführen."
    entry["status"] = status
    entry["status_text"] = STATUS_TEXT[status]
    return entry


# Ab so vielen Tagen vor Ablauf des TrueNAS-Zertifikats erscheint ein Hinweis
# (eine Stelle für Prüfer, Benachrichtigung und Diagnose).
CERT_WARN_DAYS = notify.CERT_WARN_DAYS


def apply_cert_expiry(entry: dict, not_after, now: datetime | None = None) -> None:
    """Trägt das Ablaufdatum des Zertifikats ein und warnt rechtzeitig vorher.

    Wird das Zertifikat auf dem TrueNAS erneuert, ändert sich der Fingerabdruck
    und die Verbindung wird (gewollt) abgelehnt. Die Vorwarnung erklärt das,
    bevor es passiert.
    """
    if not_after is None:
        return
    now = now or datetime.now(timezone.utc)
    days = (not_after - now).days
    entry["cert_expires"] = not_after.date().isoformat()
    entry["cert_days_left"] = days
    date_text = not_after.strftime("%d.%m.%Y")
    if days < 0:
        entry["notices"].append(
            f"Das Zertifikat dieses TrueNAS ist seit {date_text} abgelaufen. Wird es erneuert, "
            "im Assistenten \"Zertifikats-Fingerabdruck neu prüfen\" wählen.")
    elif days <= CERT_WARN_DAYS:
        entry["notices"].append(
            f"Zertifikat läuft in {days} Tagen ab ({date_text}). Danach ändert sich vermutlich "
            "der Fingerabdruck - dann im Assistenten \"Zertifikats-Fingerabdruck neu prüfen\".")


def check_system(cfg, secret, connect=Client.connect, now: datetime | None = None) -> dict:
    """Prüft EIN System. Gibt immer einen Eintrag zurück (wirft nicht)."""
    tag = f"[{cfg.name}]"
    try:
        with connect(cfg) as client:
            not_after = getattr(client.transport, "cert_not_after", None)
            client.login(cfg.username, secret)
            raw = collect(client)
        entry = build_entry(cfg, raw)
        apply_cert_expiry(entry, not_after, now)
        return entry
    except FingerprintMismatch:
        log.warning("%s Zertifikats-Fingerabdruck stimmt nicht überein - Verbindung abgebrochen, "
                    "Key NICHT gesendet.", tag)
        return offline_entry(
            cfg, "fingerprint",
            "Zertifikats-Fingerabdruck stimmt nicht überein. Entweder wurde das Zertifikat "
            "auf dem TrueNAS erneuert (dann neuen Fingerabdruck prüfen und eintragen) oder "
            "jemand gibt sich als TrueNAS aus. Assistent oder Diagnose ausführen.")
    except AuthFailed as exc:
        log.warning("%s %s", tag, secret.redact(str(exc)))
        return offline_entry(cfg, "auth", secret.redact(str(exc)))
    except MethodNotAllowed as exc:  # Programmierfehler - sollte nie passieren
        log.error("%s %s", tag, exc)
        return offline_entry(cfg, "error", "Interner Fehler: verbotene Methode verweigert.")
    except RPCError as exc:
        msg = secret.redact(str(exc))
        log.warning("%s TrueNAS-Fehler: %s", tag, msg)
        return offline_entry(cfg, "error", f"TrueNAS hat mit einem Fehler geantwortet: {msg}")
    except ConnectError as exc:
        log.info("%s Nicht erreichbar: %s", tag, secret.redact(str(exc)))
        return offline_entry(cfg, "unreachable", f"Nicht erreichbar: {secret.redact(str(exc))}")
    except Exception as exc:  # unerwartet: trotzdem nur "offline", nie abstürzen
        log.error("%s Unerwarteter Fehler (%s)", tag, type(exc).__name__)
        return offline_entry(cfg, "error", f"Unerwarteter Fehler ({type(exc).__name__}).")


def aggregate(entries: list, ignored=()) -> str:
    """Gesamtstatus über alle Systeme.

    DIESELBE Regel steht im Widget (plasmoid/.../logic.js, aggregate). Beide
    werden mit denselben Fällen getestet: tests/aggregate_cases.json.
    "ignored" = Kennungen mit "offline ignorieren" (stellt nur das Widget ein;
    der Prüfer selbst übergibt nichts).

    - Ein System: dessen Status (offline = grau, wie bisher).
    - Mehrere: schlimmster Status der erreichbaren Systeme. Ein System mit
      falschem Fingerabdruck zählt immer mindestens als Warnung. Ein sonst
      offline System zählt als Warnung, wenn es mindestens 2 Prüfungen in Folge
      fehlte UND ein anderes System erreichbar ist. Sind alle offline: grau.
    """
    if not entries:
        return "offline"
    if len(entries) == 1:
        return entries[0]["status"]
    reachable = [e for e in entries if e["status"] != "offline"]
    status = "ok" if reachable else "offline"
    for e in reachable:
        if _RANK[e["status"]] > _RANK.get(status, -1):
            status = e["status"]
    for e in entries:
        if e["status"] != "offline":
            continue
        counts = e.get("offline_kind") == "fingerprint" or (
            reachable and e.get("offline_count", 0) >= 2 and e["id"] not in ignored)
        if counts and (status == "offline" or _RANK[status] < _RANK["warning"]):
            status = "warning"
    return status


def build_status(app, entries: list, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    status = aggregate(entries)
    return {
        "schema": STATUS_SCHEMA,
        "checked_at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "checked_at_epoch": int(now),
        # Für das Widget: ab wann status.json als "nicht mehr frisch" gilt.
        "interval_minutes": app.interval_minutes if app else None,
        "status": status,
        "status_text": STATUS_TEXT[status],
        "reason": None if entries else "Noch kein TrueNAS eingerichtet. Rechtsklick auf das "
                                       "Widget -> \"TrueNAS hinzufügen/verwalten…\".",
        "systems": entries,
    }


def update_offline_counts(entries: list, path) -> None:
    """Zählt pro System, wie oft es nacheinander offline war (Zustandsdatei)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        if not isinstance(old, dict):
            old = {}
    except (OSError, ValueError):
        old = {}
    new = {}
    for e in entries:
        if e["status"] == "offline":
            e["offline_count"] = int(old.get(e["id"], 0) or 0) + 1
            new[e["id"]] = e["offline_count"]
        else:
            e["offline_count"] = 0
    notify.write_json_atomic(path, new)


def run_all(app, load_key=None, connect=Client.connect, now: float | None = None,
            offline_state=None) -> tuple[dict, list]:
    """Prüft alle Systeme parallel. Gibt (status, geladene Secrets) zurück."""
    load_key = load_key or keystore.load_key
    secrets = []
    jobs = []
    entries_by_id = {}
    for system_id, msg in app.broken:
        cfg = config_mod.SystemConfig(id=system_id, name=system_id, host="-", port=443,
                                      username="", fingerprint="")
        entry = offline_entry(cfg, "config", f"Konfigurationsfehler: {msg}")
        entry["web_url"] = None
        entries_by_id[system_id] = entry
        log.error("[%s] Konfiguration: %s", system_id, msg)
    for cfg in app.systems:
        try:
            secret = load_key(cfg)
        except keystore.KeyError_ as exc:
            log.error("[%s] API-Key: %s", cfg.name, exc)
            entries_by_id[cfg.id] = offline_entry(cfg, "key", f"API-Key nicht lesbar: {exc}")
            continue
        secrets.append(secret)
        jobs.append((cfg, secret))
    if jobs:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {cfg.id: pool.submit(check_system, cfg, secret, connect) for cfg, secret in jobs}
            for system_id, fut in futures.items():
                entries_by_id[system_id] = fut.result()
    # Reihenfolge wie die Dateien (alphabetisch nach id)
    entries = [entries_by_id[k] for k in sorted(entries_by_id)]
    update_offline_counts(entries, offline_state or paths.offline_file())
    return build_status(app, entries, now), secrets


def redact_all(text: str, secrets) -> str:
    for s in secrets:
        text = s.redact(text)
    return text


def write_status(status: dict, secrets=()) -> None:
    # Letzte Sicherung: falls ein Key irgendwo im Text gelandet wäre.
    public = json.loads(redact_all(json.dumps(status, ensure_ascii=False), secrets))
    notify.write_json_atomic(paths.status_file(), public)


class _RedactFilter(logging.Filter):
    """Ersetzt jeden Key in jeder Log-Zeile durch *** (zusätzliche Sicherung)."""

    def __init__(self, secrets):
        super().__init__()
        self.secrets = secrets

    def filter(self, record):
        record.msg = redact_all(record.getMessage(), self.secrets)
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
    secrets: list = []
    handler.addFilter(_RedactFilter(secrets))
    try:
        app = config_mod.load()
    except config_mod.ConfigError as exc:
        log.error("Konfiguration: %s", exc)
        status = build_status(None, [], None)
        status["reason"] = f"Konfigurationsfehler: {exc}"
        write_status(status)
        return 2
    if not app.systems and not app.broken:
        log.info("Noch kein TrueNAS eingerichtet (python3 -m truenas_widget.setup).")

    status, loaded = run_all(app)
    secrets.extend(loaded)
    write_status(status, secrets)
    for e in status["systems"]:
        if e["status"] == "offline":
            log.info("[%s] offline (%s)", e["name"], e.get("offline_reason"))
        else:
            log.info("[%s] %s (%d Alerts, %d App-Updates, Systemupdate: %s)",
                     e["name"], e["status"], len(e["alerts"]), len(e["app_updates"]),
                     "ja" if e["system_update"]["available"] else "nein")
    notify.process(status, paths.notified_file(), app.notifications)
    return 0


if __name__ == "__main__":
    sys.exit(main())
