"""Konfiguration lesen und streng prüfen.

Die Konfiguration ist eine TOML-Datei (einfaches Textformat, siehe
config.example.toml im Repo). Python bringt dafür seit Version 3.11 das
Modul "tomllib" mit, es wird also nichts zusätzlich installiert.

Sicherheitsregeln, die hier erzwungen werden:
- Nur verschlüsselte Verbindungen. Steht in "host" etwas wie "http://" oder
  "ws://", bricht das Programm mit einer klaren Fehlermeldung ab.
  Grund: TrueNAS widerruft einen API-Key sofort, wenn er über eine
  unverschlüsselte Verbindung benutzt wird.
- Ein Zertifikats-Fingerabdruck (SHA-256) MUSS eingetragen sein. Ohne ihn
  wird keine Verbindung aufgebaut (Ausnahme: das Diagnose-Skript darf den
  Fingerabdruck anzeigen, sendet dabei aber keinen Key).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths


class ConfigError(Exception):
    """Fehler in der Konfiguration. Die Meldung ist für Menschen gedacht."""


MISSING_HOST_MSG = (
    "Abschnitt [truenas] fehlt oder 'host' darin fehlt. "
    "Bitte in config.toml eintragen, z. B.:\n  [truenas]\n  host = \"192.168.1.20\""
)


# Unsichere Adress-Präfixe, die wir ausdrücklich ablehnen.
_INSECURE_SCHEMES = ("http://", "ws://")
# Sichere Präfixe, die wir tolerieren und einfach entfernen.
_SECURE_SCHEMES = ("https://", "wss://")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Erlaubte Prüfintervalle in Minuten. Der systemd-Timer benutzt OnCalendar=
# (nur damit wirkt Persistent=true, siehe systemd.timer(5)). Ein Kalender-
# Ausdruck wie "*:00/15" ist nur dann gleichmässig, wenn das Intervall glatt
# in eine Stunde (bzw. ab 60 Minuten glatt in einen Tag) passt.
ALLOWED_INTERVALS = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30,
                     60, 120, 180, 240, 360, 480, 720, 1440)


def oncalendar(minutes: int) -> str:
    """systemd-OnCalendar-Ausdruck für das Intervall, z. B. 15 -> '*-*-* *:00/15:00'."""
    if minutes not in ALLOWED_INTERVALS:
        raise ConfigError(_interval_error(minutes))
    if minutes < 60:
        return f"*-*-* *:00/{minutes}:00"
    if minutes < 1440:
        return f"*-*-* 00/{minutes // 60}:00:00"
    return "*-*-* 00:00:00"


def _interval_error(minutes) -> str:
    return (f"'interval_minutes' = {minutes} ist nicht möglich. Erlaubt sind: "
            + ", ".join(str(m) for m in ALLOWED_INTERVALS) + " (Minuten).")


def normalize_fingerprint(value: str) -> str:
    """Macht aus "AB:CD:..." bzw. "ab cd ..." einheitlich "abcd..." (64 Zeichen)."""
    cleaned = re.sub(r"[\s:]", "", value or "").lower()
    if cleaned.startswith("sha256"):
        cleaned = cleaned[len("sha256"):].lstrip("=")
    return cleaned


def format_fingerprint(hex_digest: str) -> str:
    """Macht aus "abcd..." die gut lesbare Form "AB:CD:..."."""
    h = hex_digest.upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def parse_host(raw: str) -> str:
    """Prüft das Feld "host" und gibt nur den Rechnernamen bzw. die IP zurück.

    - "http://..." oder "ws://..." -> Fehler (unverschlüsselt!)
    - "https://..." oder "wss://..." -> Präfix wird entfernt
    - Pfade oder Ports im host-Feld sind nicht erlaubt (Port hat ein eigenes Feld).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(MISSING_HOST_MSG)
    host = raw.strip()
    lower = host.lower()
    for scheme in _INSECURE_SCHEMES:
        if lower.startswith(scheme):
            raise ConfigError(
                f"Unsichere Adresse '{scheme}...' abgelehnt. Es ist nur HTTPS erlaubt. "
                "TrueNAS widerruft einen API-Key sofort, wenn er unverschlüsselt "
                "gesendet wird. Bitte nur die Adresse (z. B. 192.168.1.20) eintragen."
            )
    for scheme in _SECURE_SCHEMES:
        if lower.startswith(scheme):
            host = host[len(scheme):]
            break
    if "://" in host:
        raise ConfigError("Unbekanntes Adress-Präfix in 'host'. Bitte nur die Adresse eintragen.")
    host = host.rstrip("/")
    if "/" in host or "?" in host or "@" in host or not host:
        raise ConfigError("'host' darf nur die Adresse enthalten (ohne Pfad, Benutzer usw.).")
    # IPv6 in eckigen Klammern erlauben ([fd00::1]), sonst keinen Port im Host.
    if host.startswith("["):
        if not host.endswith("]"):
            raise ConfigError("IPv6-Adresse in 'host' bitte so schreiben: [fd00::1]")
    elif host.count(":") == 1:
        raise ConfigError("Bitte den Port nicht in 'host' schreiben, sondern in das Feld 'port'.")
    return host


@dataclass
class Config:
    name: str
    host: str
    port: int
    username: str
    fingerprint: str  # normalisiert: 64 Hex-Zeichen, klein
    api_version: str = "v25.10.0"
    key_source: str = "file"  # "file" oder "secret-tool"
    key_file: Path = field(default_factory=paths.default_key_file)
    secret_tool_attributes: dict = field(
        default_factory=lambda: {"service": "truenas-widget", "account": "api-key"}
    )
    interval_minutes: int = 15
    timeout_seconds: float = 20.0
    notifications: bool = True

    @property
    def ws_path(self) -> str:
        # Belegt im TrueNAS-Quellcode 25.10.7 (middlewared/main.py):
        # Route "/api/{version}", z. B. /api/v25.10.0 oder /api/current.
        return f"/api/{self.api_version}"

    @property
    def ws_url(self) -> str:
        return f"wss://{self.host}:{self.port}{self.ws_path}"

    @property
    def web_url(self) -> str:
        # Adresse der TrueNAS-Weboberfläche (für den Klick im Widget).
        return f"https://{self.host}:{self.port}/"


def _get(section: dict, key: str, typ, default=None, required=False):
    if key not in section:
        if required:
            raise ConfigError(f"In der Konfiguration fehlt '{key}'.")
        return default
    value = section[key]
    if typ is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if not isinstance(value, typ) or (typ is int and isinstance(value, bool)):
        raise ConfigError(f"'{key}' hat den falschen Typ (erwartet: {typ.__name__}).")
    return value


def from_dict(data: dict, *, require_fingerprint: bool = True) -> Config:
    """Baut eine geprüfte Config aus den gelesenen TOML-Daten."""
    tn = data.get("truenas", {})
    key = data.get("key", {})
    chk = data.get("checker", {})
    notif = data.get("notifications", {})
    for name, sec in (("truenas", tn), ("key", key), ("checker", chk), ("notifications", notif)):
        if not isinstance(sec, dict):
            raise ConfigError(f"Abschnitt [{name}] ist fehlerhaft.")

    # Falls jemand eine komplette URL einträgt (z. B. url = "http://..."), auch prüfen.
    if "url" in tn:
        parse_host(_get(tn, "url", str))
        raise ConfigError("Bitte statt 'url' die Felder 'host' und 'port' verwenden.")

    if "host" not in tn:
        # Häufigster Fall: Abschnitt vergessen oder "host" ausserhalb von [truenas].
        raise ConfigError(MISSING_HOST_MSG)
    host = parse_host(_get(tn, "host", str))
    port = _get(tn, "port", int, default=443)
    if not 1 <= port <= 65535:
        raise ConfigError("'port' muss zwischen 1 und 65535 liegen.")
    name = _get(tn, "name", str, default="TrueNAS").strip() or "TrueNAS"
    username = _get(tn, "username", str, default="").strip()
    if require_fingerprint and not username:
        raise ConfigError("In der Konfiguration fehlt 'username' (Benutzer, dem der API-Key gehört).")

    fp_raw = _get(tn, "fingerprint_sha256", str, default="")
    fingerprint = normalize_fingerprint(fp_raw)
    if fingerprint == "0" * 64:
        # Platzhalter aus config.example.toml gilt als "nicht eingetragen".
        fingerprint = ""
    if fingerprint and not _HEX64.match(fingerprint):
        raise ConfigError(
            "'fingerprint_sha256' ist ungültig. Erwartet werden 64 Hex-Zeichen "
            "(mit oder ohne Doppelpunkte). Das Diagnose-Skript zeigt den richtigen Wert."
        )
    if require_fingerprint and not fingerprint:
        raise ConfigError(
            "Kein Zertifikats-Fingerabdruck eingetragen ('fingerprint_sha256'). "
            "Ohne ihn wird aus Sicherheitsgründen keine Verbindung aufgebaut. "
            "Das Diagnose-Skript zeigt den aktuellen Fingerabdruck an."
        )

    api_version = _get(tn, "api_version", str, default="v25.10.0").strip()
    if not re.match(r"^(current|v\d+\.\d+(\.\d+)?)$", api_version):
        raise ConfigError("'api_version' muss z. B. 'v25.10.0' oder 'current' sein.")

    key_source = _get(key, "source", str, default="file")
    if key_source not in ("file", "secret-tool"):
        raise ConfigError("[key] source muss 'file' oder 'secret-tool' sein.")
    key_file = Path(_get(key, "file", str, default=str(paths.default_key_file()))).expanduser()
    attrs = _get(key, "secret_tool_attributes", dict,
                 default={"service": "truenas-widget", "account": "api-key"})
    if not attrs or not all(isinstance(k, str) and isinstance(v, str) for k, v in attrs.items()):
        raise ConfigError("[key] secret_tool_attributes muss Text-Paare enthalten.")

    interval = _get(chk, "interval_minutes", int, default=15)
    if interval not in ALLOWED_INTERVALS:
        raise ConfigError(_interval_error(interval))
    timeout = _get(chk, "timeout_seconds", float, default=20.0)
    if not 1 <= timeout <= 300:
        raise ConfigError("'timeout_seconds' muss zwischen 1 und 300 liegen.")

    notifications = _get(notif, "enabled", bool, default=True)

    return Config(
        name=name, host=host, port=port, username=username, fingerprint=fingerprint,
        api_version=api_version, key_source=key_source, key_file=key_file,
        secret_tool_attributes=dict(attrs), interval_minutes=interval,
        timeout_seconds=timeout, notifications=notifications,
    )


def load(path: Path | None = None, *, require_fingerprint: bool = True) -> Config:
    """Liest die Konfigurationsdatei und gibt eine geprüfte Config zurück."""
    path = Path(path) if path else paths.config_file()
    if not path.exists():
        raise ConfigError(
            f"Konfigurationsdatei nicht gefunden: {path}\n"
            "Vorlage: config.example.toml aus dem Repo nach dorthin kopieren und anpassen."
        )
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Konfigurationsdatei ist fehlerhaft: {exc}") from None
    return from_dict(data, require_fingerprint=require_fingerprint)


if __name__ == "__main__":
    # Kleine Helfer für install.sh:
    #   --print-interval    Prüfintervall in Minuten
    #   --print-oncalendar  passender systemd-OnCalendar-Ausdruck
    # Ist die Konfiguration (noch) fehlerhaft, gilt der Standard von 15 Minuten.
    import sys

    def _interval() -> int:
        try:
            return load(require_fingerprint=False).interval_minutes
        except ConfigError as exc:
            print(f"Hinweis: {exc} -> verwende 15 Minuten.", file=sys.stderr)
            return 15

    if sys.argv[1:] == ["--print-interval"]:
        print(_interval())
    elif sys.argv[1:] == ["--print-oncalendar"]:
        print(oncalendar(_interval()))
    else:
        print("Aufruf: python3 -m truenas_widget.config --print-interval | --print-oncalendar")
