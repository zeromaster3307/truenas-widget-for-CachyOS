"""Tests: Konfiguration - http:// / ws:// werden abgelehnt, Fingerabdruck Pflicht."""

import unittest

from truenas_widget import config as c

BASE = {"truenas": {"host": "192.168.1.20", "port": 5443, "username": "u",
                    "fingerprint_sha256": "AB:" * 31 + "AB"}}


def cfg_with(**tn):
    data = {"truenas": dict(BASE["truenas"], **tn)}
    return c.from_dict(data)


class ConfigTests(unittest.TestCase):
    def test_gueltige_konfiguration(self):
        cfg = cfg_with()
        self.assertEqual(cfg.ws_url, "wss://192.168.1.20:5443/api/v25.10.0")
        self.assertEqual(cfg.fingerprint, "ab" * 32)
        self.assertEqual(cfg.web_url, "https://192.168.1.20:5443/")

    def test_http_wird_abgelehnt(self):
        with self.assertRaises(c.ConfigError) as ctx:
            cfg_with(host="http://192.168.1.20")
        self.assertIn("Unsichere Adresse", str(ctx.exception))
        self.assertIn("widerruft", str(ctx.exception))

    def test_ws_wird_abgelehnt(self):
        with self.assertRaises(c.ConfigError):
            cfg_with(host="ws://192.168.1.20")
        with self.assertRaises(c.ConfigError):
            cfg_with(host="  HTTP://nas.local")

    def test_url_feld_mit_http_wird_abgelehnt(self):
        data = {"truenas": dict(BASE["truenas"], url="http://192.168.1.20:5443")}
        with self.assertRaises(c.ConfigError) as ctx:
            c.from_dict(data)
        self.assertIn("Unsichere Adresse", str(ctx.exception))

    def test_https_praefix_wird_entfernt(self):
        self.assertEqual(cfg_with(host="https://nas.local/").host, "nas.local")

    def test_port_im_host_abgelehnt(self):
        with self.assertRaises(c.ConfigError):
            cfg_with(host="192.168.1.20:5443")

    def test_ohne_fingerabdruck_keine_verbindung(self):
        with self.assertRaises(c.ConfigError):
            cfg_with(fingerprint_sha256="")

    def test_platzhalter_fingerabdruck_gilt_als_leer(self):
        with self.assertRaises(c.ConfigError):
            cfg_with(fingerprint_sha256=":".join(["00"] * 32))

    def test_ungueltiger_fingerabdruck(self):
        with self.assertRaises(c.ConfigError):
            cfg_with(fingerprint_sha256="1234")

    def test_beispielkonfiguration_ist_gueltig_aber_ohne_fingerabdruck(self):
        import tomllib
        from pathlib import Path
        data = tomllib.loads((Path(__file__).parent.parent / "config.example.toml").read_text())
        with self.assertRaises(c.ConfigError):
            c.from_dict(data)  # Platzhalter-Fingerabdruck -> muss erst ersetzt werden
        cfg = c.from_dict(data, require_fingerprint=False)
        self.assertEqual(cfg.port, 5443)
        self.assertEqual(cfg.interval_minutes, 15)
        self.assertTrue(cfg.notifications)

    def test_ungueltiges_intervall(self):
        data = {"truenas": BASE["truenas"], "checker": {"interval_minutes": 0}}
        with self.assertRaises(c.ConfigError):
            c.from_dict(data)

    def test_benachrichtigungen_abschaltbar(self):
        data = {"truenas": BASE["truenas"], "notifications": {"enabled": False}}
        self.assertFalse(c.from_dict(data).notifications)


if __name__ == "__main__":
    unittest.main()
