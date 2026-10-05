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
from .helpers import KeyLeakTestCase, make_app, make_cfg
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
            st = checker.check_system(make_cfg(port=srv.port, fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "ok", st)
        self.assertEqual(srv.received_methods, ["auth.login_ex", "alert.list", "app.query", "update.status"])

    def test_zertifikat_laeuft_bald_ab(self):
        # Das Test-Zertifikat gilt nur 1 Tag -> Vorwarnung, Farbe bleibt grün
        with self.server() as srv:
            st = checker.check_system(make_cfg(port=srv.port, fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "ok")
        self.assertIn(st["cert_days_left"], (0, 1))
        self.assertIn("Zertifikat läuft in", st["notices"][0])

    def test_kritisch_end_to_end(self):
        data = fx.scenario(**{"alert.list": [fx.alert("CRITICAL")]})
        with self.server(data) as srv:
            st = checker.check_system(make_cfg(port=srv.port, fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "critical")

    def test_nicht_erreichbar(self):
        st = checker.check_system(make_cfg(port=free_port(), fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "offline")
        self.assertEqual(st["offline_kind"], "unreachable")
        self.assertIn("Nicht erreichbar", st["offline_reason"])

    def test_falscher_fingerabdruck(self):
        with self.server() as srv:
            st = checker.check_system(make_cfg(port=srv.port, fingerprint="ab" * 32), self.secret())
        self.assertEqual(st["status"], "offline")
        self.assertEqual(st["offline_kind"], "fingerprint")
        self.assertIn("Fingerabdruck", st["offline_reason"])
        self.assertIn("Zertifikat", st["offline_reason"])  # Hinweis auf möglichen Zertifikatswechsel
        self.assertEqual(srv.app_bytes_received, 0, "Bei falschem Fingerabdruck darf nichts gesendet werden")
        self.assertEqual(srv.received_methods, [])

    def test_fehlende_rechte(self):
        with self.server(denied={"app.query"}) as srv:
            st = checker.check_system(make_cfg(port=srv.port, fingerprint=self.certs.fingerprint), self.secret())
        self.assertEqual(st["status"], "offline")  # sonst alles ok, aber unvollständig
        self.assertIn("apps", st["incomplete"])
        self.assertTrue(any("Zugriff verweigert" in p for p in st["problems"]))

    def test_falscher_key(self):
        with self.server(api_key="anderer-key") as srv:
            st = checker.check_system(make_cfg(port=srv.port, fingerprint=self.certs.fingerprint), self.secret())
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
            (cdir / "keys").mkdir(parents=True, mode=0o700)
            os.chmod(cdir, 0o700)
            os.chmod(cdir / "keys", 0o700)
            (cdir / "keys" / "test-nas").write_text(TEST_KEY + "\n")
            os.chmod(cdir / "keys" / "test-nas", 0o600)
            (cdir / "systems").mkdir()
            (cdir / "systems" / "test-nas.toml").write_text(
                'name = "Test-NAS"\nhost = "127.0.0.1"\n'
                f"port = {srv.port}\n"
                'username = "widget-leser"\n'
                f'fingerprint_sha256 = "{self.certs.fingerprint}"\n'
                "timeout_seconds = 5\n"
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
        self.assertEqual(status["schema"], 2)
        nas = status["systems"][0]
        self.assertEqual(nas["name"], "Test-NAS")
        self.assertTrue(nas["system_update"]["available"])
        # Warnung + Systemupdate + Zertifikat läuft bald ab (Test-Zertifikat gilt 1 Tag)
        self.assertEqual(len(sent), 3)
        self.assertIn("Test-NAS: Zertifikat läuft bald ab", [t for _, t, _ in sent])
        for text in (status_text, stderr.getvalue(), stdout.getvalue(), diag_out.getvalue()):
            self.assertNotIn(TEST_KEY, text)
        d = diag_out.getvalue()
        self.assertEqual(drc, 0, d)
        self.assertIn("Anmeldung: erfolgreich", d)
        self.assertIn("alle erwarteten Felder vorhanden: ja", d)
        self.assertIn("Zertifikat gültig bis:", d)
        self.assertIn("system.reboot wird verweigert", d)
        self.assertNotIn("127.0.0.1", d)          # keine Adresse in der Diagnose
        self.assertNotIn("widget-leser", d)       # kein Benutzername
        self.assertNotIn("tank", d)               # keine Werte, nur Feldnamen

    def write_system(self, tmp, port, fingerprint, key=TEST_KEY):
        root = Path(tmp)
        os.chmod(root, 0o700)
        keyfile = root / "key"
        keyfile.write_text(key)
        os.chmod(keyfile, 0o600)
        (root / "systems").mkdir()
        (root / "systems" / "test-nas.toml").write_text(
            f'host = "127.0.0.1"\nport = {port}\nusername = "widget-leser"\n'
            f'fingerprint_sha256 = "{fingerprint}"\n[key]\nfile = "{keyfile}"\n')
        return ["--config", str(root / "config.toml"), "--systems-dir", str(root / "systems")]

    def test_diagnose_zeigt_zugriff_verweigert(self):
        with tempfile.TemporaryDirectory() as tmp, self.server(denied={"update.status"}) as srv:
            args = self.write_system(tmp, srv.port, self.certs.fingerprint)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = diagnose.main(args)
        self.assertEqual(rc, 1)
        self.assertIn("Zugriff verweigert", out.getvalue())
        self.assertNotIn(TEST_KEY, out.getvalue())

    def test_diagnose_zeigt_keine_adresse_bei_fehler(self):
        """Fehlermeldungen wie "Keine Verbindung zu <host>:<port>" ohne Adresse ausgeben."""
        with tempfile.TemporaryDirectory() as tmp:
            args = self.write_system(tmp, free_port(), self.certs.fingerprint)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = diagnose.main(args)
        self.assertEqual(rc, 1)
        self.assertIn("NICHT erreichbar", out.getvalue())
        self.assertIn("<adresse>:<port>", out.getvalue())
        self.assertNotIn("127.0.0.1", out.getvalue())

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
            args = self.write_system(tmp, srv.port, "cd" * 32)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = diagnose.main(args)
        self.assertEqual(rc, 1)
        self.assertIn("stimmt NICHT", out.getvalue())
        self.assertIn("Key wird NICHT gesendet", out.getvalue())
        self.assertEqual(srv.received_methods, [])

    def test_zwei_systeme_eins_offline(self):
        """Ein System erreichbar, eins nicht: parallel geprüft, nach 2 Fehlschlägen Warnung."""
        nas = None
        with tempfile.TemporaryDirectory() as tmp, self.server() as srv:
            state = Path(tmp) / "offline.json"
            good = make_cfg(port=srv.port, fingerprint=self.certs.fingerprint, system_id="homelab", name="homelab")
            gone = make_cfg(port=free_port(), fingerprint=self.certs.fingerprint, system_id="remote", name="remote")
            app = make_app(good, gone)
            load_key = lambda cfg: keystore.Secret(TEST_KEY)
            first, _ = checker.run_all(app, load_key=load_key, offline_state=state)
            second, _ = checker.run_all(app, load_key=load_key, offline_state=state)
        self.assertEqual([e["id"] for e in first["systems"]], ["homelab", "remote"])
        self.assertEqual(first["systems"][0]["status"], "ok")
        self.assertEqual(first["systems"][1]["offline_count"], 1)
        self.assertEqual(first["status"], "ok")          # einmal weg: noch keine Warnung
        self.assertEqual(second["systems"][1]["offline_count"], 2)
        self.assertEqual(second["status"], "warning")    # zweimal weg: Warnung

    def test_kaputtes_system_und_fehlender_key(self):
        with tempfile.TemporaryDirectory() as tmp, self.server() as srv:
            good = make_cfg(port=srv.port, fingerprint=self.certs.fingerprint, system_id="homelab")
            nokey = make_cfg(port=srv.port, fingerprint=self.certs.fingerprint, system_id="ohne-key",
                             key={"file": str(Path(tmp) / "gibtsnicht")})
            app = make_app(good, nokey)
            app.broken.append(("kaputt", "In systems/kaputt.toml fehlt 'host'"))
            status, _ = checker.run_all(app, offline_state=Path(tmp) / "o.json",
                                        load_key=lambda cfg: keystore.load_key(cfg) if cfg.id == "ohne-key"
                                        else keystore.Secret(TEST_KEY))
        kinds = {e["id"]: (e["status"], e["offline_kind"]) for e in status["systems"]}
        self.assertEqual(kinds["homelab"], ("ok", None))
        self.assertEqual(kinds["ohne-key"], ("offline", "key"))
        self.assertEqual(kinds["kaputt"], ("offline", "config"))

if __name__ == "__main__":
    unittest.main()
