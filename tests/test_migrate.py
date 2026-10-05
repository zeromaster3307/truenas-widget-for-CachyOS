"""Tests: Umstellung der Konfiguration von 0.3 (ein System) auf 0.4."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from truenas_widget import config as c
from truenas_widget import migrate

from .mock_truenas import TEST_KEY

OLD = """[truenas]
name = "homelab"
host = "192.168.1.20"
port = 443
username = "widget-leser"
fingerprint_sha256 = "{fp}"

[key]
source = "file"
file = "{keyfile}"

[checker]
interval_minutes = 30
timeout_seconds = 25

[notifications]
enabled = false
"""


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "config.toml"
        self.sysdir = self.root / "systems"
        self.notified = self.root / "notified.json"
        self.keyfile = self.root / "api-key"
        self.keyfile.write_text(TEST_KEY)
        os.chmod(self.keyfile, 0o600)
        self.cfg.write_text(OLD.format(fp="AB:" * 31 + "AB", keyfile=self.keyfile))

    def tearDown(self):
        self.tmp.cleanup()

    def run_migrate(self):
        return migrate.migrate(self.cfg, self.sysdir, self.notified)

    def test_umstellung(self):
        self.notified.write_text(json.dumps({"alerts": ["uuid-1"], "apps": ["jellyfin@1.2.4"],
                                             "system": ["25.10.8"]}))
        msgs = self.run_migrate()
        self.assertTrue(msgs)
        app = c.load(self.cfg, self.sysdir)
        self.assertEqual(app.interval_minutes, 30)
        self.assertFalse(app.notifications)
        self.assertEqual(len(app.systems), 1)
        nas = app.systems[0]
        self.assertEqual((nas.id, nas.name, nas.host, nas.port), ("homelab", "homelab", "192.168.1.20", 443))
        self.assertEqual(nas.fingerprint, "ab" * 32)
        self.assertEqual(nas.timeout_seconds, 25)
        self.assertEqual(nas.key_file, self.keyfile)          # Key bleibt, wo er ist
        self.assertEqual(self.keyfile.read_text(), TEST_KEY)  # und unverändert
        self.assertEqual(os.stat(self.sysdir / "homelab.toml").st_mode & 0o777, 0o600)
        self.assertTrue((self.root / "config.toml.v0.3.bak").exists())
        state = json.loads(self.notified.read_text())
        self.assertEqual(state["apps"], ["homelab:jellyfin@1.2.4"])
        self.assertEqual(state["alerts"], ["homelab:uuid-1"])
        for path in self.root.rglob("*.toml"):
            self.assertNotIn(TEST_KEY, path.read_text())

    def test_nur_einmal(self):
        self.run_migrate()
        self.assertEqual(self.run_migrate(), [])  # neues Format: nichts zu tun
        self.assertEqual(len(list(self.sysdir.glob("*.toml"))), 1)

    def test_ohne_config_nichts_zu_tun(self):
        self.cfg.unlink()
        self.assertEqual(self.run_migrate(), [])


if __name__ == "__main__":
    unittest.main()
