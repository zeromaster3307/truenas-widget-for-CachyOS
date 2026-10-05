"""Desktop-Benachrichtigungen - jedes Ereignis genau einmal.

Was als "schon gemeldet" gilt, steht in der Zustandsdatei
~/.local/state/truenas-widget/notified.json, je System mit "<id>:" davor:
    {"alerts": ["homelab:<Alert-ID>"], "apps": ["homelab:app@version"],
     "system": ["homelab:version"]}

Ablauf bei jeder erfolgreichen Prüfung:
  1. Aktuelle Ereignisse ermitteln (Alerts ab WARNING, App-Updates, Systemupdate).
  2. Alles, was noch nicht in der Zustandsdatei steht, ist NEU -> Benachrichtigung.
  3. Zustandsdatei auf den aktuellen Stand setzen. Verschwundene Einträge
     fliegen dabei raus (ein später wieder auftretendes Ereignis würde dann
     erneut gemeldet - das ist gewollt).
Ist TrueNAS nicht erreichbar, wird die Zustandsdatei NICHT verändert und
es gibt keine Benachrichtigung.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "TrueNAS-Status"


def load_state(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {k: set(data.get(k, [])) for k in SECTIONS}
    except (OSError, ValueError):
        pass
    return {k: set() for k in SECTIONS}


def save_state(path: Path, state: dict) -> None:
    write_json_atomic(path, {k: sorted(v) for k, v in state.items()})


def write_json_atomic(path: Path, data) -> None:
    """Schreibt JSON so, dass Leser nie eine halbe Datei sehen."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


SECTIONS = ("alerts", "apps", "system")


def _app_key(a: dict) -> str:
    return f'{a["name"]}@{a.get("new") or "image:" + str(a.get("current"))}'


def event_keys(entry: dict) -> dict:
    """Schlüssel aller aktuell meldewürdigen Ereignisse EINES Systems.

    Jeder Schlüssel beginnt mit "<system-id>:", damit gleiche App-Namen oder
    Versionen auf verschiedenen Systemen getrennt gemerkt werden.
    """
    p = entry["id"] + ":"
    keys = {
        "alerts": {p + a["id"] for a in entry.get("alerts", [])},
        "apps": {p + _app_key(a) for a in entry.get("app_updates", [])},
        "system": set(),
    }
    su = entry.get("system_update") or {}
    if su.get("available"):
        keys["system"].add(p + str(su.get("new_version") or "?"))
    return keys


def build_messages(entry: dict, new: dict) -> list[tuple[str, str, str]]:
    """Baut (Dringlichkeit, Titel, Text) für alle neuen Ereignisse eines Systems."""
    name = entry.get("name", "TrueNAS")
    p = entry["id"] + ":"
    messages = []
    new_alerts = [a for a in entry.get("alerts", []) if p + a["id"] in new["alerts"]]
    if new_alerts:
        critical = any(a.get("severity") == "critical" for a in new_alerts)
        body = "\n".join(f'{a["level"]}: {a["text"]}' for a in new_alerts[:5])
        if len(new_alerts) > 5:
            body += f"\n+ {len(new_alerts) - 5} weitere"
        title = f"{name}: {'Kritische Meldung' if critical else 'Warnung'}"
        messages.append(("critical" if critical else "normal", title, body))
    new_apps = [a for a in entry.get("app_updates", []) if p + _app_key(a) in new["apps"]]
    if new_apps:
        lines = [f'{a["name"]}: {a.get("current")} → {a.get("new") or "neues Image"}' for a in new_apps[:5]]
        if len(new_apps) > 5:
            lines.append(f"+ {len(new_apps) - 5} weitere")
        messages.append(("normal", f"{name}: App-Updates verfügbar", "\n".join(lines)))
    if new["system"]:
        su = entry.get("system_update") or {}
        messages.append(("normal", f"{name}: Systemupdate verfügbar",
                         f'Neue Version: {su.get("new_version") or "unbekannt"}'))
    return messages


def send_notification(urgency: str, title: str, body: str) -> bool:
    exe = shutil.which("notify-send")
    if not exe:
        log.warning("notify-send nicht gefunden - Benachrichtigung entfällt (Paket 'libnotify').")
        return False
    try:
        subprocess.run(
            [exe, f"--app-name={APP_NAME}", f"--urgency={urgency}",
             "--icon=network-server", title, body],
            check=False, timeout=10, capture_output=True,
        )
        return True
    except (OSError, subprocess.TimeoutExpired):
        log.warning("Benachrichtigung konnte nicht gesendet werden.")
        return False


def process(status: dict, state_path: Path, enabled: bool, sender=None) -> list:
    """Meldet neue Ereignisse aller Systeme und aktualisiert die Zustandsdatei.

    Gibt die Liste der (gesendeten bzw. bei abgeschalteten Benachrichtigungen
    unterdrückten) Meldungen zurück. Ein System, das offline ist, löst nichts
    aus; sein bisheriger Stand bleibt gemerkt.
    """
    old = load_state(state_path)
    current = {k: set() for k in SECTIONS}
    messages = []
    for entry in status.get("systems", []):
        prefix = entry["id"] + ":"
        mine_old = {k: {x for x in old.get(k, set()) if x.startswith(prefix)} for k in SECTIONS}
        if entry.get("status") == "offline":
            for k in SECTIONS:
                current[k] |= mine_old[k]  # nichts vergessen, nichts melden
            continue
        keys = event_keys(entry)
        new = {k: keys[k] - mine_old[k] for k in SECTIONS}
        if any(new.values()):
            messages += build_messages(entry, new)
        # Unvollständige Daten (z. B. Zugriff verweigert auf eine Methode):
        # für diese Bereiche den alten Stand behalten, damit nichts doppelt kommt.
        for section in entry.get("incomplete", []):
            keys[section] |= mine_old[section]
        for k in SECTIONS:
            current[k] |= keys[k]
    if enabled:
        sender = sender or send_notification
        for urgency, title, body in messages:
            sender(urgency, title, body)
    save_state(state_path, current)
    return messages
