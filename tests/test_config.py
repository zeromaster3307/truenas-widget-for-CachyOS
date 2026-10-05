# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests: Konfiguration (config.toml + systems/<id>.toml).

http:// / ws:// werden abgelehnt, Fingerabdruck ist Pflicht, ein kaputtes
System legt die anderen nicht lahm.
"""

import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from truenas_widget import config as c

REPO = Path(__file__).parent.parent
SYS = {"name": "homelab", "host": "192.168.1.20", "port": 443, "username": "u",
       "fingerprint_sha256": "AB:" * 31 + "AB"}


def system(**over):
    data = dict(SYS, **over)
    return c.system_from_dict("homelab", data)


class SystemConfigTests(unittest.TestCase):
    def test_gueltiges_system(self):
        cfg = system()
        self.assertEqual(cfg.ws_url, "wss://192.168.1.20:443/api/v25.10.0")
        self.assertEqual(cfg.fingerprint, "ab" * 32)
        self.assertEqual(cfg.web_url, "https://192.168.1.20:443/")
        # Standard-Key-Datei je System
        self.assertEqual(cfg.key_file.name, "homelab")
        self.assertEqual(cfg.key_file.parent.name, "keys")
        self.assertEqual(cfg.secret_tool_attributes, {"service": "truenas-widget", "account": "homelab"})

    def test_http_wird_abgelehnt(self):
        with self.assertRaises(c.ConfigError) as ctx:
            system(host="http://192.168.1.20")
        self.assertIn("Unsichere Adresse", str(ctx.exception))
        self.assertIn("widerruft", str(ctx.exception))

    def test_ws_wird_abgelehnt(self):
        with self.assertRaises(c.ConfigError):
            system(host="ws://192.168.1.20")
        with self.assertRaises(c.ConfigError):
            system(host="  HTTP://nas.local")

    def test_url_feld_mit_http_wird_abgelehnt(self):
        with self.assertRaises(c.ConfigError) as ctx:
            system(url="http://192.168.1.20:443")
        self.assertIn("Unsichere Adresse", str(ctx.exception))

    def test_https_praefix_wird_entfernt(self):
        self.assertEqual(system(host="https://nas.local/").host, "nas.local")

    def test_port_im_host_abgelehnt(self):
        with self.assertRaises(c.ConfigError):
            system(host="192.168.1.20:443")

    def test_host_fehlt(self):
        data = {k: v for k, v in SYS.items() if k != "host"}
        with self.assertRaises(c.ConfigError) as ctx:
            c.system_from_dict("remote", data)
        self.assertIn("In systems/remote.toml fehlt 'host'", str(ctx.exception))

    def test_ohne_fingerabdruck_keine_verbindung(self):
        with self.assertRaises(c.ConfigError):
            system(fingerprint_sha256="")

    def test_platzhalter_fingerabdruck_gilt_als_leer(self):
        with self.assertRaises(c.ConfigError):
            system(fingerprint_sha256=":".join(["00"] * 32))

    def test_ungueltiger_fingerabdruck(self):
        with self.assertRaises(c.ConfigError):
            system(fingerprint_sha256="1234")

    def test_ungueltige_kennung(self):
        for bad in ("Homelab", "mein nas", "", "a" * 40, "-x"):
            with self.subTest(bad=bad):
                with self.assertRaises(c.ConfigError):
                    c.system_from_dict(bad, dict(SYS))

    def test_kennung_aus_namen(self):
        self.assertEqual(c.make_id("Mein NAS (Büro)"), "mein-nas-buero")
        self.assertEqual(c.make_id("homelab", {"homelab", "homelab-2"}), "homelab-3")
        self.assertEqual(c.make_id("!!!"), "truenas")

    def test_beispieldatei_ist_gueltig_aber_ohne_fingerabdruck(self):
        data = tomllib.loads((REPO / "system.example.toml").read_text())
        with self.assertRaises(c.ConfigError):
            c.system_from_dict("beispiel", data)  # Platzhalter-Fingerabdruck
        cfg = c.system_from_dict("beispiel", data, require_fingerprint=False)
        self.assertEqual(cfg.port, 443)

    def test_schreiben_und_wieder_lesen(self):
        cfg = system(name='Büro "NAS"', port=8443)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "systems" / "homelab.toml"
            c.write_private(path, c.system_to_toml(cfg))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            again = c.load_system(path)
        self.assertEqual(again, cfg)

    def test_secret_tool_schreiben_und_lesen(self):
        cfg = system(key={"source": "secret-tool"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "homelab.toml"
            c.write_private(path, c.system_to_toml(cfg))
            again = c.load_system(path)
        self.assertEqual(again.key_source, "secret-tool")
        self.assertEqual(again.secret_tool_attributes, {"service": "truenas-widget", "account": "homelab"})


class GlobalConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "config.toml"
        self.sysdir = self.root / "systems"

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        return c.load(self.cfg, self.sysdir)

    def write_system(self, sid, **over):
        cfg = c.system_from_dict(sid, dict(SYS, name=sid, **over))
        c.write_private(self.sysdir / f"{sid}.toml", c.system_to_toml(cfg))

    def test_ohne_dateien_keine_systeme(self):
        app = self.load()
        self.assertEqual(app.systems, [])
        self.assertEqual(app.interval_minutes, 15)
        self.assertTrue(app.notifications)

    def test_mehrere_systeme_alphabetisch(self):
        self.write_system("remote")
        self.write_system("homelab")
        self.assertEqual([s.id for s in self.load().systems], ["homelab", "remote"])

    def test_kaputtes_system_stoert_die_anderen_nicht(self):
        self.write_system("homelab")
        self.sysdir.joinpath("remote.toml").write_text('host = "http://192.168.1.20"\n')
        app = self.load()
        self.assertEqual([s.id for s in app.systems], ["homelab"])
        self.assertEqual(app.broken[0][0], "remote")
        self.assertIn("Unsichere Adresse", app.broken[0][1])

    def test_beispiel_config_ist_gueltig(self):
        app = c.global_from_dict(tomllib.loads((REPO / "config.example.toml").read_text()))
        self.assertEqual(app.interval_minutes, 15)

    def test_ungueltiges_intervall(self):
        self.cfg.write_text("[checker]\ninterval_minutes = 0\n")
        with self.assertRaises(c.ConfigError):
            self.load()

    def test_benachrichtigungen_abschaltbar(self):
        self.cfg.write_text("[notifications]\nenabled = false\n")
        self.assertFalse(self.load().notifications)

    def test_vorlage_global(self):
        app = c.global_from_dict(tomllib.loads(c.global_to_toml(30, False)))
        self.assertEqual((app.interval_minutes, app.notifications), (30, False))


if __name__ == "__main__":
    unittest.main()
