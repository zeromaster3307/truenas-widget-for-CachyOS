"""Gemeinsame Test-Hilfen."""

import io
import logging
import unittest

from truenas_widget import config as config_mod

from .mock_truenas import TEST_KEY


def make_cfg(port=5443, fingerprint="ab" * 32, **over):
    data = {
        "truenas": {"name": "Test-NAS", "host": "127.0.0.1", "port": port,
                    "username": "widget-leser", "fingerprint_sha256": fingerprint,
                    "api_version": "v25.10.0"},
        "checker": {"timeout_seconds": 5},
        "notifications": {"enabled": True},
    }
    for section, values in over.items():
        data.setdefault(section, {}).update(values)
    return config_mod.from_dict(data)


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
