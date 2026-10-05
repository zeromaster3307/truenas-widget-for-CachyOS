"""Diagnose-Skript (nur lesend).

Prüft Schritt für Schritt (für jedes eingerichtete System):
  1. Konfiguration lesbar? (config.toml + systems/*.toml)
  2. TrueNAS erreichbar? Welcher Zertifikats-Fingerabdruck? Stimmt er?
  3. API-Key lesbar, Rechte der Key-Datei in Ordnung?
  4. Anmeldung klappt?
  5. Für jede benutzte Methode: Welche FELDNAMEN und DATENTYPEN kommen zurück?
     (Es werden KEINE Werte ausgegeben - nur Namen und Typen.)
     Fehlen Rechte, steht dort "Zugriff verweigert".
  6. Selbsttest der Whitelist (eine Schreibmethode muss verweigert werden).

Die Ausgabe enthält keinen Key, keine Adresse und keinen Benutzernamen
(nur die Kennungen der Systeme, z. B. "homelab") und kann daher zum
Abgleich weitergegeben werden.

Aufruf:
  ./diagnose.sh                       vollständige Diagnose aller Systeme
  ./diagnose.sh --system remote       nur ein System (Kennung = Dateiname in systems/)
  ./diagnose.sh --nur-fingerabdruck --system remote   nur den Fingerabdruck (sendet nichts)
  ./diagnose.sh --nur-fingerabdruck --host 192.168.1.20 --port 443
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from . import config as config_mod
from . import keystore
from .checker import APP_FIELDS, evaluate_alerts, evaluate_apps, evaluate_system_update
from .rpc import ALLOWED_METHODS, AuthFailed, Client, MethodNotAllowed, RPCError
from .wsclient import ConnectError, FingerprintMismatch, WebSocket, fetch_fingerprint

# Felder, die der Prüfer tatsächlich benutzt - werden gezielt abgeglichen.
EXPECTED = {
    "alert.list": ["uuid", "id", "level", "dismissed", "formatted", "text", "klass"],
    "app.query": APP_FIELDS,
    # status.new_version.version kann nur geprüft werden, wenn ein Update da ist.
    "update.status": ["code", "status", "status.new_version", "error"],
}

MAX_DEPTH = 4
MAX_FIELDS = 60


def out(text: str = "") -> None:
    print(text, flush=True)


# ---------------- Schema (nur Namen + Typen) ermitteln ----------------

def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _new_node():
    return {"types": set(), "fields": {}, "items": None, "count": 0}


def build_schema(value, node=None, depth=0):
    """Fasst die Struktur zusammen (bei Listen über alle Elemente)."""
    node = node or _new_node()
    t = _type_name(value)
    node["types"].add(t)
    node["count"] += 1
    if depth >= MAX_DEPTH:
        return node
    if t == "dict":
        for k, v in value.items():
            node["fields"][str(k)] = build_schema(v, node["fields"].get(str(k)), depth + 1)
    elif t == "list":
        node.setdefault("lengths", []).append(len(value))
        for item in value:
            node["items"] = build_schema(item, node["items"], depth + 1)
    return node


def render_schema(node, indent="    ", name="(Antwort)") -> list[str]:
    lines = []
    types = " | ".join(sorted(node["types"]))
    extra = ""
    if "list" in node["types"] and node.get("lengths"):
        extra = f"  [{sum(node['lengths'])} Einträge]"
    lines.append(f"{indent}{name}: {types}{extra}")
    child_indent = indent + "    "
    if node["fields"]:
        items = sorted(node["fields"].items())
        for fname, child in items[:MAX_FIELDS]:
            lines += render_schema(child, child_indent, fname)
        if len(items) > MAX_FIELDS:
            lines.append(f"{child_indent}... (+{len(items) - MAX_FIELDS} weitere Felder)")
    if node["items"] is not None:
        lines += render_schema(node["items"], child_indent, "[jedes Element]")
    return lines


def has_path(node, dotted: str) -> bool:
    """Prüft, ob ein Feld (z. B. status.new_version) in der Struktur vorkommt."""
    if node is None:
        return False
    if "list" in node["types"] and node["items"] is not None and not node["fields"]:
        node = node["items"]
    for part in dotted.split("."):
        if part not in node["fields"]:
            return False
        node = node["fields"][part]
    return True


# ---------------- Ablauf ----------------

def section(n, title):
    out()
    out(f"[{n}] {title}")


def fingerprint_only(host, port, system_id, timeout) -> int:
    if not host:
        try:
            app = config_mod.load(require_fingerprint=False)
        except config_mod.ConfigError as exc:
            out(f"FEHLER: {exc}")
            return 2
        systems = [x for x in app.systems if system_id in (None, x.id)]
        if len(systems) != 1:
            out("Bitte das System angeben: --system <id>  (vorhanden: "
                + (", ".join(x.id for x in app.systems) or "keins") + ")")
            out("oder die Adresse direkt: --host <adresse> --port <port>")
            return 2
        host, port = systems[0].host, port or systems[0].port
    else:
        try:
            host = config_mod.parse_host(host)
        except config_mod.ConfigError as exc:
            out(f"FEHLER: {exc}")
            return 2
    try:
        fp = fetch_fingerprint(host, port or 443, timeout)
    except ConnectError as exc:
        out(f"FEHLER: {exc}")
        return 1
    out("SHA-256-Fingerabdruck des TrueNAS-Zertifikats (es wurde nichts gesendet):")
    out()
    out("    " + config_mod.format_fingerprint(fp))
    out()
    out("Bitte VOR dem Eintragen vergleichen, z. B. im Browser: TrueNAS-Seite öffnen ->")
    out("Schloss-/Warnsymbol neben der Adresse -> Zertifikat anzeigen -> SHA-256-Fingerabdruck.")
    out("Erst wenn beide gleich sind, in ~/.config/truenas-widget/systems/<id>.toml")
    out("bei fingerprint_sha256 eintragen (oder den Assistenten benutzen).")
    return 0


def diagnose_system(cfg) -> int:
    """Diagnose für EIN System. Gibt die Anzahl der Hinweise zurück."""
    problems = 0
    out()
    out("=" * 60)
    out(f"System '{cfg.id}'")
    out("=" * 60)
    out(f"    Port: {cfg.port} | API-Pfad: {cfg.ws_path} | Key-Quelle: {cfg.key_source}")
    out(f"    Benutzername eingetragen: {'ja' if cfg.username else 'NEIN'}")
    out(f"    Fingerabdruck eingetragen: {'ja' if cfg.fingerprint else 'NEIN'}")

    section("2", "Erreichbarkeit und Zertifikat")
    try:
        actual = fetch_fingerprint(cfg.host, cfg.port, cfg.timeout_seconds)
    except ConnectError as exc:
        out(f"    NICHT erreichbar: {exc}")
        out("    -> Gleiches Netz? TrueNAS an? Adresse/Port in der Konfiguration richtig?")
        return problems + 1
    out("    erreichbar: ja (TLS-Verbindung steht)")
    out(f"    aktueller Fingerabdruck (SHA-256): {config_mod.format_fingerprint(actual)}")
    if not cfg.fingerprint:
        out("    In der Konfiguration ist noch KEIN Fingerabdruck eingetragen.")
        out("    -> Wert oben prüfen (siehe README Abschnitt 4) und eintragen. Danach erneut starten.")
        out("    Abbruch: Ohne bestätigten Fingerabdruck wird der Key nicht gesendet.")
        return problems + 1
    if actual != cfg.fingerprint:
        out("    Fingerabdruck stimmt NICHT mit der Konfiguration überein!")
        out("    -> Zertifikat erneuert? Dann neuen Wert prüfen und eintragen.")
        out("    -> Sonst: Vorsicht, evtl. gibt sich ein anderes Gerät als TrueNAS aus.")
        out("    Abbruch: Der Key wird NICHT gesendet.")
        return problems + 1
    out("    Fingerabdruck stimmt mit der Konfiguration überein: ja")

    section("3", "API-Key")
    if cfg.key_source == "file":
        warnings = keystore.check_permissions(cfg.key_file)
        for w in warnings:
            out(f"    WARNUNG: {w}")
            problems += 1
    try:
        secret = keystore.load_key(cfg)
    except keystore.KeyError_ as exc:
        out(f"    FEHLER: {exc}")
        return problems + 1
    out(f"    gelesen: ja (Quelle: {cfg.key_source}, Länge und Inhalt werden nicht angezeigt)")
    if not cfg.username:
        out("    FEHLER: 'username' fehlt in der Konfiguration.")
        return problems + 1

    section("4", "Anmeldung (auth.login_ex, Mechanismus API_KEY_PLAIN)")
    try:
        ws = WebSocket.connect(cfg.host, cfg.port, cfg.ws_path, cfg.fingerprint, cfg.timeout_seconds)
    except FingerprintMismatch:
        out("    Fingerabdruck hat sich zwischenzeitlich geändert - Abbruch.")
        return problems + 1
    except ConnectError as exc:
        out(f"    FEHLER beim WebSocket-Aufbau: {secret.redact(str(exc))}")
        return problems + 1
    with Client(ws) as client:
        try:
            client.login(cfg.username, secret)
        except AuthFailed as exc:
            out(f"    FEHLER: {exc}")
            return problems + 1
        except (RPCError, ConnectError) as exc:
            out(f"    FEHLER: {secret.redact(str(exc))}")
            return problems + 1
        out("    Anmeldung: erfolgreich")

        section("5", "Abfragen (nur Feldnamen und Datentypen)")
        results = {}
        for method, params in (
            ("alert.list", []),
            ("app.query", [[], {"select": APP_FIELDS}]),
            ("update.status", []),
        ):
            out()
            shown = method + (f"  (mit select: {', '.join(APP_FIELDS)})" if method == "app.query" else "")
            out(f"  {shown}")
            try:
                value = client.call(method, params)
            except RPCError as exc:
                if exc.access_denied:
                    out("    Zugriff verweigert - dem Benutzer fehlt die passende Leserolle.")
                else:
                    out(f"    FEHLER: {secret.redact(str(exc))}")
                problems += 1
                continue
            except ConnectError as exc:
                out(f"    FEHLER: {secret.redact(str(exc))}")
                problems += 1
                continue
            node = build_schema(value)
            results[method] = value
            for line in render_schema(node):
                out(secret.redact(line))
            missing = [f for f in EXPECTED[method] if not has_path(node, f)]
            if isinstance(value, list) and not value:
                out("    (leere Liste - Feldabgleich nicht möglich)")
            elif missing:
                out(f"    FEHLENDE erwartete Felder: {', '.join(missing)}")
                problems += 1
            else:
                out("    alle erwarteten Felder vorhanden: ja")

    section("6", "Auswertung (nur Anzahlen)")
    if "alert.list" in results:
        raw = results["alert.list"] or []
        levels = {}
        for a in raw if isinstance(raw, list) else []:
            if isinstance(a, dict):
                key = f"{a.get('level')}{' (quittiert)' if a.get('dismissed') else ''}"
                levels[key] = levels.get(key, 0) + 1
        out(f"    Alerts gesamt: {len(raw) if isinstance(raw, list) else '?'} | nach Stufe: "
            + (", ".join(f"{k}={v}" for k, v in sorted(levels.items())) or "keine"))
        out(f"    davon zählen (nicht quittiert, ab WARNING): {len(evaluate_alerts(raw))}")
    if "app.query" in results:
        apps = results["app.query"] or []
        out(f"    Apps installiert: {len(apps) if isinstance(apps, list) else '?'} | "
            f"mit Update: {len(evaluate_apps(apps))}")
    if "update.status" in results:
        info, problem = evaluate_system_update(results["update.status"])
        out(f"    Systemupdate verfügbar: {'ja' if info['available'] else 'nein'}")
        if problem:
            out(f"    Hinweis: {secret.redact(problem)}")

    return problems


def full(system_id=None, config_path=None, systems_dir=None) -> int:
    problems = 0
    out(f"TrueNAS-Widget Diagnose (nur lesend), Version {__version__}")
    out("Diese Ausgabe enthält KEINE Schlüssel, Adressen oder Werte - nur Feldnamen und Typen.")

    section("1", "Konfiguration")
    try:
        app = config_mod.load(config_path, systems_dir, require_fingerprint=False)
    except config_mod.ConfigError as exc:
        out(f"    FEHLER: {exc}")
        return 2
    out(f"    Benachrichtigungen: {'an' if app.notifications else 'aus'} | Intervall: {app.interval_minutes} min")
    out(f"    Systeme: {len(app.systems)} gültig, {len(app.broken)} fehlerhaft")
    for bad_id, msg in app.broken:
        out(f"    FEHLER in systems/{bad_id}.toml: {msg}")
        problems += 1
    systems = [x for x in app.systems if system_id in (None, x.id)]
    if system_id and not systems:
        out(f"    FEHLER: kein System mit der Kennung '{system_id}'.")
        return 2
    if not app.systems and not app.broken:
        out("    Noch kein TrueNAS eingerichtet. Assistent: python3 -m truenas_widget.setup")
        problems += 1
    for cfg in systems:
        problems += diagnose_system(cfg)

    out()
    section(7, "Selbsttest Whitelist")

    class _NoSend:
        def send_text(self, _):
            raise AssertionError("darf nie aufgerufen werden")

        def recv_text(self):
            raise AssertionError("darf nie aufgerufen werden")

        def close(self):
            pass

    try:
        Client(_NoSend()).call("system.reboot")
        out("    FEHLER: Schreibmethode wurde NICHT verweigert!")
        problems += 1
    except MethodNotAllowed:
        out("    system.reboot wird verweigert (nichts gesendet): ja")
    out(f"    erlaubte Methoden: {', '.join(sorted(ALLOWED_METHODS))}")

    out()
    out("Ergebnis: " + ("alles in Ordnung." if problems == 0 else f"{problems} Hinweis(e), siehe oben."))
    return 0 if problems == 0 else 1


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="    WARNUNG: %(message)s")
    p = argparse.ArgumentParser(description="TrueNAS-Widget Diagnose (nur lesend)")
    p.add_argument("--nur-fingerabdruck", action="store_true",
                   help="nur den Zertifikats-Fingerabdruck anzeigen (sendet nichts)")
    p.add_argument("--host", help="Adresse (nur mit --nur-fingerabdruck)")
    p.add_argument("--port", type=int, help="Port (nur mit --nur-fingerabdruck)")
    p.add_argument("--system", help="nur dieses System prüfen (Kennung = Dateiname in systems/)")
    p.add_argument("--config", type=Path, help=argparse.SUPPRESS)        # für Tests
    p.add_argument("--systems-dir", type=Path, help=argparse.SUPPRESS)   # für Tests
    args = p.parse_args(argv)
    if args.nur_fingerabdruck:
        return fingerprint_only(args.host, args.port, args.system, 15.0)
    if args.host or args.port:
        p.error("--host/--port nur zusammen mit --nur-fingerabdruck")
    return full(args.system, args.config, args.systems_dir)


if __name__ == "__main__":
    sys.exit(main())
