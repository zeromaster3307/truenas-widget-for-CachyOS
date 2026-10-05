# SPDX-License-Identifier: GPL-3.0-or-later
"""Alle Dateipfade an einer Stelle.

Es werden die üblichen XDG-Verzeichnisse benutzt (wie bei fast allen
Linux-Programmen). Ist eine XDG-Variable nicht gesetzt, gilt der Standard:

    Konfiguration : ~/.config/truenas-widget/config.toml      (allgemeine Einstellungen)
    Systeme       : ~/.config/truenas-widget/systems/<id>.toml (je TrueNAS eine Datei)
    API-Keys      : ~/.config/truenas-widget/keys/<id>        (je TrueNAS eine Datei, Rechte 600)
    Status-Datei  : ~/.cache/truenas-widget/status.json   (liest das Widget)
    Zustandsdateien: ~/.local/state/truenas-widget/notified.json
                    (merkt sich, was schon gemeldet wurde) und offline.json
                    (wie oft ein System nacheinander nicht erreichbar war)
"""

import os
from pathlib import Path

APP_DIR_NAME = "truenas-widget"


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / default


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_DIR_NAME


def config_file() -> Path:
    return config_dir() / "config.toml"


def systems_dir() -> Path:
    return config_dir() / "systems"


def keys_dir() -> Path:
    return config_dir() / "keys"


def default_key_file(system_id: str) -> Path:
    return keys_dir() / system_id


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / APP_DIR_NAME


def status_file() -> Path:
    return cache_dir() / "status.json"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / APP_DIR_NAME


def notified_file() -> Path:
    return state_dir() / "notified.json"


def offline_file() -> Path:
    return state_dir() / "offline.json"
