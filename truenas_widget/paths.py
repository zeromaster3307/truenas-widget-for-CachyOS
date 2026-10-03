"""Alle Dateipfade an einer Stelle.

Es werden die üblichen XDG-Verzeichnisse benutzt (wie bei fast allen
Linux-Programmen). Ist eine XDG-Variable nicht gesetzt, gilt der Standard:

    Konfiguration : ~/.config/truenas-widget/config.toml
    API-Key-Datei : ~/.config/truenas-widget/api-key
    Status-Datei  : ~/.cache/truenas-widget/status.json   (liest das Widget)
    Zustandsdatei : ~/.local/state/truenas-widget/notified.json
                    (merkt sich, was schon gemeldet wurde)
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


def default_key_file() -> Path:
    return config_dir() / "api-key"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / APP_DIR_NAME


def status_file() -> Path:
    return cache_dir() / "status.json"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / APP_DIR_NAME


def notified_file() -> Path:
    return state_dir() / "notified.json"
