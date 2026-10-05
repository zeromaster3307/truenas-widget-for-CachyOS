# SPDX-License-Identifier: GPL-3.0-or-later
"""Konfiguration lesen und streng prüfen (ab Version 0.4: mehrere Systeme).

Aufbau (alles TOML, siehe config.example.toml und system.example.toml im Repo):

  ~/.config/truenas-widget/config.toml        allgemeine Einstellungen
      [checker]       interval_minutes
      [notifications] enabled
  ~/.config/truenas-widget/systems/<id>.toml  je TrueNAS eine Datei
      name, host, port, username, fingerprint_sha256, api_version,
      timeout_seconds, [key] ...

<id> ist der Dateiname ohne ".toml" (z. B. "homelab"). Er dient intern als
Kennung, z. B. für die Key-Datei keys/<id> und für "schon gemeldet".
Der Einrichtungs-Assistent (python3 -m truenas_widget.setup) legt diese
Dateien an; man kann sie aber auch von Hand schreiben.

Python bringt für TOML seit Version 3.11 das Modul "tomllib" mit.

Sicherheitsregeln, die hier erzwungen werden:
- Nur verschlüsselte Verbindungen. Steht in "host" etwas wie "http://" oder
  "ws://", wird das System mit einer klaren Fehlermeldung abgelehnt.
  Grund: TrueNAS widerruft einen API-Key sofort, wenn er über eine
  unverschlüsselte Verbindung benutzt wird.
- Ein Zertifikats-Fingerabdruck (SHA-256) MUSS eingetragen sein. Ohne ihn
  wird keine Verbindung aufgebaut (Ausnahme: Diagnose und Assistent dürfen
  den Fingerabdruck anzeigen, senden dabei aber keinen Key).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths


class ConfigError(Exception):
    """Fehler in der Konfiguration. Die Meldung ist für Menschen gedacht."""


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



_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

DEFAULT_API_VERSION = "v25.10.0"
DEFAULT_TIMEOUT = 20.0


def valid_id(system_id: str) -> bool:
    return bool(_ID_RE.match(system_id or ""))


def make_id(name: str, taken=()) -> str:
    """Macht aus einem Anzeigenamen eine Kennung, z. B. "Mein NAS" -> "mein-nas"."""
    repl = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    base = "".join(repl.get(c, c) for c in (name or "").lower())
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:28] or "truenas"
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def parse_host(raw: str) -> str:
    """Prüft das Feld "host" und gibt nur den Rechnernamen bzw. die IP zurück.

    - "http://..." oder "ws://..." -> Fehler (unverschlüsselt!)
    - "https://..." oder "wss://..." -> Präfix wird entfernt
    - Pfade oder Ports im host-Feld sind nicht erlaubt (Port hat ein eigenes Feld).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("Es fehlt die Adresse des TrueNAS (host).")
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
class SystemConfig:
    """Ein TrueNAS-System."""
    id: str
    name: str
    host: str
    port: int
    username: str
    fingerprint: str  # normalisiert: 64 Hex-Zeichen, klein
    api_version: str = DEFAULT_API_VERSION
    key_source: str = "file"  # "file" oder "secret-tool"
    key_file: Path | None = None
    secret_tool_attributes: dict = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_TIMEOUT

    def __post_init__(self):
        if self.key_file is None:
            self.key_file = paths.default_key_file(self.id)
        if not self.secret_tool_attributes:
            self.secret_tool_attributes = {"service": "truenas-widget", "account": self.id}

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


@dataclass
class AppConfig:
    """Allgemeine Einstellungen plus alle Systeme."""
    interval_minutes: int = 15
    notifications: bool = True
    systems: list = field(default_factory=list)
    # Systeme mit fehlerhafter Datei: (id, Fehlermeldung). Die übrigen laufen weiter.
    broken: list = field(default_factory=list)


def _get(section: dict, key: str, typ, default=None, where: str = "Konfiguration"):
    if key not in section:
        return default
    value = section[key]
    if typ is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if not isinstance(value, typ) or (typ is int and isinstance(value, bool)):
        raise ConfigError(f"'{key}' in {where} hat den falschen Typ (erwartet: {typ.__name__}).")
    return value


def system_from_dict(system_id: str, data: dict, *, require_fingerprint: bool = True,
                     where: str | None = None) -> SystemConfig:
    """Baut ein geprüftes System aus den gelesenen TOML-Daten einer systems/<id>.toml."""
    where = where or f"systems/{system_id}.toml"
    if not valid_id(system_id):
        raise ConfigError(
            f"Ungültiger Dateiname {where}: erlaubt sind Kleinbuchstaben, Ziffern, '-' und '_' "
            "(max. 32 Zeichen), z. B. systems/homelab.toml."
        )
    key = data.get("key", {})
    if not isinstance(key, dict):
        raise ConfigError(f"Abschnitt [key] in {where} ist fehlerhaft.")

    # Falls jemand eine komplette URL einträgt (z. B. url = "http://..."), auch prüfen.
    if "url" in data:
        parse_host(_get(data, "url", str, where=where))
        raise ConfigError(f"Bitte in {where} statt 'url' die Felder 'host' und 'port' verwenden.")
    if "host" not in data or not str(data.get("host", "")).strip():
        raise ConfigError(f"In {where} fehlt 'host' (Adresse des TrueNAS), z. B. host = \"192.168.1.20\".")
    host = parse_host(_get(data, "host", str, where=where))
    port = _get(data, "port", int, default=443, where=where)
    if not 1 <= port <= 65535:
        raise ConfigError(f"'port' in {where} muss zwischen 1 und 65535 liegen.")
    name = _get(data, "name", str, default=system_id, where=where).strip() or system_id
    username = _get(data, "username", str, default="", where=where).strip()
    if require_fingerprint and not username:
        raise ConfigError(f"In {where} fehlt 'username' (Benutzer, dem der API-Key gehört).")

    fingerprint = normalize_fingerprint(_get(data, "fingerprint_sha256", str, default="", where=where))
    if fingerprint == "0" * 64:
        fingerprint = ""  # Platzhalter aus system.example.toml gilt als "nicht eingetragen"
    if fingerprint and not _HEX64.match(fingerprint):
        raise ConfigError(
            f"'fingerprint_sha256' in {where} ist ungültig. Erwartet werden 64 Hex-Zeichen "
            "(mit oder ohne Doppelpunkte). Diagnose oder Assistent zeigen den richtigen Wert."
        )
    if require_fingerprint and not fingerprint:
        raise ConfigError(
            f"In {where} ist kein Zertifikats-Fingerabdruck eingetragen ('fingerprint_sha256'). "
            "Ohne ihn wird aus Sicherheitsgründen keine Verbindung aufgebaut."
        )

    api_version = _get(data, "api_version", str, default=DEFAULT_API_VERSION, where=where).strip()
    if not re.match(r"^(current|v\d+\.\d+(\.\d+)?)$", api_version):
        raise ConfigError(f"'api_version' in {where} muss z. B. 'v25.10.0' oder 'current' sein.")
    timeout = _get(data, "timeout_seconds", float, default=DEFAULT_TIMEOUT, where=where)
    if not 1 <= timeout <= 120:
        raise ConfigError(f"'timeout_seconds' in {where} muss zwischen 1 und 120 liegen.")

    key_source = _get(key, "source", str, default="file", where=where)
    if key_source not in ("file", "secret-tool"):
        raise ConfigError(f"[key] source in {where} muss 'file' oder 'secret-tool' sein.")
    key_file_raw = _get(key, "file", str, default="", where=where)
    key_file = Path(key_file_raw).expanduser() if key_file_raw else None
    attrs = _get(key, "secret_tool_attributes", dict, default={}, where=where)
    if attrs and not all(isinstance(k, str) and isinstance(v, str) for k, v in attrs.items()):
        raise ConfigError(f"[key] secret_tool_attributes in {where} muss Text-Paare enthalten.")

    return SystemConfig(
        id=system_id, name=name, host=host, port=port, username=username,
        fingerprint=fingerprint, api_version=api_version, key_source=key_source,
        key_file=key_file, secret_tool_attributes=dict(attrs), timeout_seconds=timeout,
    )


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} ist fehlerhaft: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"{path} kann nicht gelesen werden ({exc.strerror}).") from None


def load_system(path: Path, *, require_fingerprint: bool = True) -> SystemConfig:
    path = Path(path)
    return system_from_dict(path.stem, _read_toml(path), require_fingerprint=require_fingerprint,
                            where=f"systems/{path.name}")


def global_from_dict(data: dict) -> AppConfig:
    chk = data.get("checker", {})
    notif = data.get("notifications", {})
    for name, sec in (("checker", chk), ("notifications", notif)):
        if not isinstance(sec, dict):
            raise ConfigError(f"Abschnitt [{name}] in config.toml ist fehlerhaft.")
    interval = _get(chk, "interval_minutes", int, default=15, where="config.toml")
    if interval not in ALLOWED_INTERVALS:
        raise ConfigError(_interval_error(interval))
    notifications = _get(notif, "enabled", bool, default=True, where="config.toml")
    return AppConfig(interval_minutes=interval, notifications=notifications)


def load(config_path: Path | None = None, systems_dir: Path | None = None, *,
         require_fingerprint: bool = True) -> AppConfig:
    """Liest config.toml (optional) und alle systems/*.toml.

    Ein fehlerhaftes System bricht nicht alles ab: es landet in AppConfig.broken.
    Ein Fehler in config.toml selbst wirft ConfigError.
    """
    config_path = Path(config_path) if config_path else paths.config_file()
    systems_dir = Path(systems_dir) if systems_dir else paths.systems_dir()
    app = global_from_dict(_read_toml(config_path)) if config_path.exists() else AppConfig()
    if systems_dir.is_dir():
        for path in sorted(systems_dir.glob("*.toml")):
            try:
                app.systems.append(load_system(path, require_fingerprint=require_fingerprint))
            except ConfigError as exc:
                app.broken.append((path.stem, str(exc)))
    return app


# ------------------------------------------------------------------------
# Schreiben (für Assistent und Umstellung). tomllib kann nur lesen, daher
# ein kleiner eigener Schreiber nur für die bekannten Felder.
# ------------------------------------------------------------------------

def _toml_str(value: str) -> str:
    # TOML-"basic strings" haben dieselben Escape-Regeln wie JSON.
    return json.dumps(str(value), ensure_ascii=False)


def system_to_toml(cfg: SystemConfig) -> str:
    lines = [
        "# TrueNAS-System für das TrueNAS-Status-Widget",
        "# Angelegt bzw. geändert vom Einrichtungs-Assistenten",
        "# (python3 -m truenas_widget.setup). Von Hand ändern ist erlaubt.",
        "",
        f"name = {_toml_str(cfg.name)}",
        f"host = {_toml_str(cfg.host)}",
        f"port = {int(cfg.port)}",
        f"username = {_toml_str(cfg.username)}",
        f"fingerprint_sha256 = {_toml_str(format_fingerprint(cfg.fingerprint))}",
        f"api_version = {_toml_str(cfg.api_version)}",
        f"timeout_seconds = {int(cfg.timeout_seconds) if float(cfg.timeout_seconds).is_integer() else cfg.timeout_seconds}",
        "",
        "[key]",
        f"source = {_toml_str(cfg.key_source)}",
    ]
    if cfg.key_source == "file":
        lines.append(f"file = {_toml_str(_home_short(cfg.key_file))}")
    else:
        attrs = ", ".join(f"{k} = {_toml_str(v)}" for k, v in cfg.secret_tool_attributes.items())
        lines.append(f"secret_tool_attributes = {{ {attrs} }}")
    return "\n".join(lines) + "\n"


def write_private(path: Path, text: str) -> None:
    """Schreibt eine Datei nur für den eigenen Benutzer lesbar (600), atomar."""
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


GLOBAL_TEMPLATE = """# Allgemeine Einstellungen des TrueNAS-Status-Widgets
# Die TrueNAS-Systeme selbst stehen je in einer Datei in systems/
# (anlegen am einfachsten mit dem Assistenten: Rechtsklick auf das Widget
# -> "TrueNAS hinzufügen/verwalten…").

[checker]
# Abstand zwischen zwei Prüfungen in Minuten.
# Erlaubt: 1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440
# Nach einer Änderung bitte ./install.sh erneut ausführen (stellt den Timer um).
interval_minutes = {interval}

[notifications]
# Desktop-Benachrichtigungen bei neuen Warnungen/Updates (true/false)
enabled = {notifications}
"""


def global_to_toml(interval_minutes: int = 15, notifications: bool = True) -> str:
    return GLOBAL_TEMPLATE.format(interval=int(interval_minutes),
                                  notifications="true" if notifications else "false")


def _home_short(path: Path) -> str:
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    # Kleine Helfer für install.sh:
    #   --print-interval    Prüfintervall in Minuten
    #   --print-oncalendar  passender systemd-OnCalendar-Ausdruck
    #   --count-systems     Anzahl gültiger Systeme
    # Ist die Konfiguration (noch) fehlerhaft, gilt der Standard von 15 Minuten.
    import sys

    def _app():
        try:
            return load(require_fingerprint=False)
        except ConfigError as exc:
            print(f"Hinweis: {exc}", file=sys.stderr)
            return AppConfig()

    if sys.argv[1:] == ["--print-interval"]:
        print(_app().interval_minutes)
    elif sys.argv[1:] == ["--print-oncalendar"]:
        print(oncalendar(_app().interval_minutes))
    elif sys.argv[1:] == ["--count-systems"]:
        print(len(_app().systems))
    else:
        print("Aufruf: python3 -m truenas_widget.config --print-interval | --print-oncalendar | --count-systems")
