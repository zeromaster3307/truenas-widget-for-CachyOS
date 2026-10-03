"""Ende-zu-Ende-Tests gegen die TrueNAS-Attrappe (echtes TLS + WebSocket lokal).

Deckt ab: alles ok, nicht erreichbar, falscher Fingerabdruck (Key wird
NICHT gesendet), fehlende Rechte, falscher Key, und dass der Key in keinem
Log, keiner Ausgabe und nicht in status.json auftaucht.
"""

import contextlib
import io
import json
import logging
import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from truenas_widget import checker, diagnose, keystore

from . import fixtures as fx
from .helpers import KeyLeakTestCase, make_cfg
from .mock_truenas import TEST_KEY, CertDir, MockTrueNAS

HAVE_OPENSSL = shutil.which("openssl") is not None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@unittest.skipUnless(HAVE_OPENSSL, "openssl wird für das Test-Zertifikat gebraucht")
class IntegrationTests(KeyLeakTestCase):
    @classmethod
    def setUpClass(cls):
        cls.certs = CertDir().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.certs.__exit__(None, None, None)

    def server(self, responses=fx.ALL_OK, **kw):
        return MockTrueNAS(self.certs.cert, self.certs.key, responses=responses, **kw)

    def secret(self):
        return keystore.Secret(TEST_KEY)

    def test_alles_ok(self):
        with self.server() as srv:
            st = checker.run_once(make_cfg(port=srv.port, fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "ok", st)
        self.assertEqual(srv.received_methods, ["auth.login_ex", "alert.list", "app.query", "update.status"])

    def test_kritisch_end_to_end(self):
        data = fx.scenario(**{"alert.list": [fx.alert("CRITICAL")]})
        with self.server(data) as srv:
            st = checker.run_once(make_cfg(port=srv.port, fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "critical")

    def test_nicht_erreichbar(self):
        st = checker.run_once(make_cfg(port=free_port(), fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "offline")
        self.assertIn("Nicht erreichbar", st["offline_reason"])

    def test_falscher_fingerabdruck(self):
        with self.server() as srv:
            st = checker.run_once(make_cfg(port=srv.port, fingerprint="ab" * 32), self.secret())
        self.assertEqual(st["status"], "offline")
        self.assertIn("Fingerabdruck", st["offline_reason"])
        self.assertIn("Zertifikat", st["offline_reason"])  # Hinweis auf möglichen Zertifikatswechsel
        self.assertEqual(srv.app_bytes_received, 0, "Bei falschem Fingerabdruck darf nichts gesendet werden")
        self.assertEqual(srv.received_methods, [])

    def test_fehlende_rechte(self):
        with self.server(denied={"app.query"}) as srv:
            st = checker.run_once(make_cfg(port=srv.port, fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "offline")  # sonst alles ok, aber unvollständig
        self.assertIn("apps", st["incomplete"])
        self.assertTrue(any("Zugriff verweigert" in p for p in st["problems"]))

    def test_falscher_key(self):
        with self.server(api_key="anderer-key") as srv:
            st = checker.run_once(make_cfg(port=srv.port, fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "offline")
        self.assertIn("Anmeldung fehlgeschlagen", st["offline_reason"])
        self.assertEqual(srv.received_methods, ["auth.login_ex"])

    def test_main_komplett_ohne_key_leck(self):
        """Kompletter Lauf wie per systemd: Config + Key-Datei, status.json, Benachrichtigung."""
        data = fx.scenario(**{"alert.list": [fx.alert("WARNING")],
                              "update.status": fx.update_status("25.10.8")})
        with tempfile.TemporaryDirectory() as tmp, self.server(data) as srv:
            env = {"XDG_CONFIG_HOME": f"{tmp}/config", "XDG_CACHE_HOME": f"{tmp}/cache",
                   "XDG_STATE_HOME": f"{tmp}/state"}
            cdir = Path(env["XDG_CONFIG_HOME"]) / "truenas-widget"
            cdir.mkdir(parents=True, mode=0o700)
            os.chmod(cdir, 0o700)
            (cdir / "api-key").write_text(TEST_KEY + "\n")
            os.chmod(cdir / "api-key", 0o600)
            (cdir / "config.toml").write_text(
                "[truenas]\n"
                'name = "Test-NAS"\nhost = "127.0.0.1"\n'
                f"port = {srv.port}\n"
                'username = "widget-leser"\n'
                f'fingerprint_sha256 = "{self.certs.fingerprint}"\n'
                "[key]\n"
                f'file = "{cdir / "api-key"}"\n'
            )
            sent = []
            stderr, stdout = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, env), \
                    mock.patch("truenas_widget.notify.send_notification",
                               side_effect=lambda *a: sent.append(a)), \
                    contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                rc = checker.main([])
                status_text = (Path(env["XDG_CACHE_HOME"]) / "truenas-widget" / "status.json").read_text()
                # Diagnose im selben Aufbau
                diag_out = io.StringIO()
                with contextlib.redirect_stdout(diag_out):
                    drc = diagnose.main([])
            # Den von main() angelegten Log-Ausgang wieder entfernen.
            logging_root = logging.getLogger()
            for h in list(logging_root.handlers):
                if h is not self._handler:
                    logging_root.removeHandler(h)
        self.assertEqual(rc, 0)
        status = json.loads(status_text)
        self.assertEqual(status["status"], "warning")
        self.assertEqual(status["system_name"], "Test-NAS")
        self.assertTrue(status["system_update"]["available"])
        self.assertEqual(len(sent), 2)  # Warnung + Systemupdate
        for text in (status_text, stderr.getvalue(), stdout.getvalue(), diag_out.getvalue()):
            self.assertNotIn(TEST_KEY, text)
        d = diag_out.getvalue()
        self.assertEqual(drc, 0, d)
        self.assertIn("Anmeldung: erfolgreich", d)
        self.assertIn("alle erwarteten Felder vorhanden: ja", d)
        self.assertIn("system.reboot wird verweigert", d)
        self.assertNotIn("127.0.0.1", d)          # keine Adresse in der Diagnose
        self.assertNotIn("widget-leser", d)       # kein Benutzername
        self.assertNotIn("tank", d)               # keine Werte, nur Feldnamen

    def test_diagnose_zeigt_zugriff_verweigert(self):
        with tempfile.TemporaryDirectory() as tmp, self.server(denied={"update.status"}) as srv:
            cfgfile = Path(tmp) / "config.toml"
            keyfile = Path(tmp) / "api-key"
            keyfile.write_text(TEST_KEY)
            os.chmod(keyfile, 0o600)
            os.chmod(tmp, 0o700)
            cfgfile.write_text(
                f'[truenas]\nhost = "127.0.0.1"\nport = {srv.port}\nusername = "widget-leser"\n'
                f'fingerprint_sha256 = "{self.certs.fingerprint}"\n[key]\nfile = "{keyfile}"\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = diagnose.main(["--config", str(cfgfile)])
        self.assertEqual(rc, 1)
        self.assertIn("Zugriff verweigert", out.getvalue())
        self.assertNotIn(TEST_KEY, out.getvalue())

    def test_diagnose_fingerabdruck(self):
        with self.server() as srv:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = diagnose.main(["--nur-fingerabdruck", "--host", "127.0.0.1", "--port", str(srv.port)])
        self.assertEqual(rc, 0)
        self.assertIn(self.certs.fingerprint.upper()[:8], out.getvalue().replace(":", ""))
        self.assertEqual(srv.app_bytes_received, 0)

    def test_diagnose_bricht_bei_falschem_fingerabdruck_ab(self):
        with tempfile.TemporaryDirectory() as tmp, self.server() as srv:
            cfgfile = Path(tmp) / "config.toml"
            cfgfile.write_text(
                f'[truenas]\nhost = "127.0.0.1"\nport = {srv.port}\nusername = "u"\n'
                f'fingerprint_sha256 = "{"cd" * 32}"\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = diagnose.main(["--config", str(cfgfile)])
        self.assertEqual(rc, 1)
        self.assertIn("stimmt NICHT", out.getvalue())
        self.assertIn("Key wird NICHT gesendet", out.getvalue())


if __name__ == "__main__":
    unittest.main()
