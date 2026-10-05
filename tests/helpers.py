# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemeinsame Test-Hilfen."""

import io
import logging
import unittest

from truenas_widget import config as config_mod

from .mock_truenas import TEST_KEY


def make_cfg(port=443, fingerprint="ab" * 32, system_id="test-nas", name="Test-NAS", **over):
    """Ein geprüftes System (wie aus systems/<id>.toml)."""
    data = {"name": name, "host": "127.0.0.1", "port": port, "username": "widget-leser",
            "fingerprint_sha256": fingerprint, "api_version": "v25.10.0", "timeout_seconds": 5}
    data.update(over)
    return config_mod.system_from_dict(system_id, data)


def make_app(*systems, interval=15, notifications=True):
    return config_mod.AppConfig(interval_minutes=interval, notifications=notifications,
                                systems=list(systems))


class KeyLeakTestCase(unittest.TestCase):
    """Basisklasse: sammelt ALLE Log-Ausgaben und prüft am Ende, dass der
    Test-Key nirgends auftaucht."""

    def setUp(self):
        self._log_stream = io.StringIO()
        self._handler = logging.StreamHandler(self._log_stream)
        self._handler.setLevel(logging.DEBUG)
        self._handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        root = logging.getLogger()
        self._old_level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(self._handler)

    def tearDown(self):
        root = logging.getLogger()
        root.removeHandler(self._handler)
        root.setLevel(self._old_level)
        self.assertNotIn(TEST_KEY, self._log_stream.getvalue(), "API-Key im Log gefunden!")

    def log_text(self) -> str:
        return self._log_stream.getvalue()
