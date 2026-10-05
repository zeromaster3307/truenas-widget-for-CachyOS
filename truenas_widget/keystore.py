# SPDX-License-Identifier: GPL-3.0-or-later
"""API-Key sicher lesen.

Zwei Wege werden unterstützt:
  a) Datei ~/.config/truenas-widget/api-key  (Standard)
     - Die Datei sollte die Rechte 600 haben (nur der eigene Benutzer darf lesen).
     - Sind die Rechte zu offen, gibt das Programm eine WARNUNG aus.
  b) Secret-Service (KWallet/libsecret) über das Programm "secret-tool".

Der Key wird in einem "Secret"-Objekt aufbewahrt. Wird dieses Objekt
versehentlich ausgegeben (print, Log, Fehlermeldung), erscheint nur "***".
Den echten Wert liefert nur reveal(), und das wird an genau einer Stelle
benutzt: beim Anmelden an TrueNAS.
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class KeyError_(Exception):
    """Der Key konnte nicht gelesen werden. Die Meldung enthält NIE den Key."""


class Secret:
    """Hülle für den API-Key, die sich beim Ausgeben selbst unkenntlich macht."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def redact(self, text: str) -> str:
        """Ersetzt den Key in einem beliebigen Text durch ***."""
        if self._value and isinstance(text, str):
            return text.replace(self._value, "***")
        return text

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __format__(self, spec) -> str:
        return "***"

    def __reduce__(self):
        # Verhindert, dass der Key versehentlich serialisiert (pickle) wird.
        raise TypeError("Secret darf nicht serialisiert werden")


def check_permissions(path: Path) -> list[str]:
    """Prüft die Rechte der Key-Datei und ihres Ordners. Gibt Warnungen zurück."""
    warnings = []
    try:
        st = path.stat()
    except OSError:
        return warnings
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077:
        warnings.append(
            f"Die Key-Datei {path} hat zu offene Rechte ({oct(mode)[2:]}). "
            f"Andere Benutzer könnten den Key lesen. Bitte ausführen: chmod 600 '{path}'"
        )
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        warnings.append(f"Die Key-Datei {path} gehört nicht dem aktuellen Benutzer.")
    try:
        dmode = stat.S_IMODE(path.parent.stat().st_mode)
        if dmode & 0o077:
            warnings.append(
                f"Der Ordner {path.parent} hat zu offene Rechte ({oct(dmode)[2:]}). "
                f"Empfohlen: chmod 700 '{path.parent}'"
            )
    except OSError:
        pass
    return warnings


def _clean(raw: str) -> str:
    # Nur führende/abschliessende Leerzeichen und Zeilenumbrüche entfernen.
    return raw.strip()


def read_key_file(path: Path) -> Secret:
    path = Path(path).expanduser()
    if not path.exists():
        raise KeyError_(
            f"Key-Datei nicht gefunden: {path}. Siehe README, Abschnitt 'Key sicher ablegen'."
        )
    for warning in check_permissions(path):
        log.warning(warning)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            value = _clean(fh.read())
    except OSError as exc:
        # Nur den Fehlertyp ausgeben, nicht den Inhalt.
        raise KeyError_(f"Key-Datei {path} kann nicht gelesen werden ({exc.strerror}).") from None
    if not value:
        raise KeyError_(f"Key-Datei {path} ist leer.")
    if "\n" in value or " " in value:
        raise KeyError_(f"Key-Datei {path} enthält mehr als eine Zeile/Wort. Bitte nur den Key eintragen.")
    return Secret(value)


def read_secret_tool(attributes: dict, timeout: float = 10.0) -> Secret:
    """Liest den Key über 'secret-tool lookup <attr> <wert> ...'."""
    args = ["secret-tool", "lookup"]
    for k, v in attributes.items():
        args += [k, v]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise KeyError_(
            "Das Programm 'secret-tool' ist nicht installiert (Paket 'libsecret')."
        ) from None
    except subprocess.TimeoutExpired:
        raise KeyError_("secret-tool hat nicht geantwortet (Wallet gesperrt?).") from None
    value = _clean(proc.stdout or "")
    if proc.returncode != 0 or not value:
        # stderr NICHT ausgeben: man weiss nie, was darin steht.
        raise KeyError_(
            "Kein Key im Secret-Service gefunden (secret-tool lookup ohne Ergebnis). "
            "Siehe README, Abschnitt 'secret-tool'."
        )
    return Secret(value)


def load_key(cfg) -> Secret:
    """Liest den Key so, wie es in der Konfiguration eingestellt ist."""
    if cfg.key_source == "secret-tool":
        return read_secret_tool(cfg.secret_tool_attributes)
    return read_key_file(cfg.key_file)
