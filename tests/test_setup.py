# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests: Einrichtungs-Assistent (hinzufügen, ändern, entfernen, Key, Fingerabdruck).

Die Oberfläche wird durch ein "Drehbuch" ersetzt (ScriptedUI), die Verbindung
läuft echt gegen die TrueNAS-Attrappe. Zusätzlich wird der kdialog-Teil mit
einem nachgebauten kdialog geprüft: der Key darf nie in den Programm-
Argumenten auftauchen.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from truenas_widget import checker, paths, setup
from truenas_widget import config as c

from . import fixtures as fx
from .helpers import KeyLeakTestCase
from .mock_truenas import TEST_KEY, CertDir, MockTrueNAS

HAVE_OPENSSL = shutil.which("openssl") is not None


class ScriptedUI:
    """Spielt vorbereitete Antworten ab und merkt sich alle angezeigten Texte."""

    def __init__(self, script):
        self.script = list(script)
        self.shown = []

    def _next(self, kind, text):
        self.shown.append((kind, text))
        if not self.script:
            raise AssertionError(f"Keine Antwort mehr vorbereitet für {kind}: {text[:60]}")
        exp_kind, value = self.script.pop(0)
        if exp_kind != kind:
            raise AssertionError(f"Erwartet {exp_kind}, aufgerufen {kind}: {text[:80]}")
        if isinstance(value, Exception):
            raise value
        return value

    def info(self, text):
        self._next("info", text)

    def error(self, text):
        self._next("error", text)

    def ask(self, text, default=""):
        value = self._next("ask", text)
        return default if value is None else value

    def secret(self, text):
        return self._next("secret", text)

    def yesno(self, text, yes="Ja", no="Nein"):
        return self._next("yesno", text)

    def choose(self, text, options):
        value = self._next("choose", text + " | " + " / ".join(t for t, _ in options))
        if value not in [t for t, _ in options]:
            raise AssertionError(f"{value} nicht im Menü: {options}")
        return value

    def all_text(self):
        return "\n".join(t for _, t in self.shown)


@unittest.skipUnless(HAVE_OPENSSL, "openssl wird für das Test-Zertifikat gebraucht")
class AssistantTests(KeyLeakTestCase):
    @classmethod
    def setUpClass(cls):
        cls.certs = CertDir().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.certs.__exit__(None, None, None)

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        env = {"XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
               "XDG_STATE_HOME": str(root / "state")}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.opened, self.started = [], []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        super().tearDown()

    def server(self, **kw):
        return MockTrueNAS(self.certs.cert, self.certs.key, responses=fx.ALL_OK, **kw)

    def assistant(self, ui, has_secret_tool=False):
        return setup.Assistant(ui, opener=self.opened.append, starter=lambda: self.started.append(1),
                               has_secret_tool=lambda: has_secret_tool)

    def add_script(self, port, key=TEST_KEY):
        return [
            ("info", None),                 # Vorbereitung auf dem TrueNAS
            ("ask", "homelab"),             # Name
            ("ask", "127.0.0.1"),           # Adresse
            ("ask", str(port)),             # Port
            ("yesno", True),                # Seite im Browser öffnen? -> ja
            ("yesno", True),                # Fingerabdruck stimmt
            ("ask", None),                  # Benutzer: Vorgabe widget-leser
            ("yesno", False),               # API-Keys-Seite öffnen? -> nein
            ("secret", key),                # Key
        ]

    def test_hinzufuegen(self):
        with self.server() as srv:
            ui = ScriptedUI(self.add_script(srv.port) + [("info", None), ("info", None)])
            self.assertEqual(self.assistant(ui).run(), 0)
        sysfile = paths.systems_dir() / "homelab.toml"
        keyfile = paths.keys_dir() / "homelab"
        cfg = c.load_system(sysfile)
        self.assertEqual((cfg.host, cfg.port, cfg.username), ("127.0.0.1", srv.port, "widget-leser"))
        self.assertEqual(cfg.fingerprint, self.certs.fingerprint)
        self.assertEqual(keyfile.read_text().strip(), TEST_KEY)
        for path in (sysfile, keyfile):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(paths.keys_dir()).st_mode & 0o777, 0o700)
        self.assertNotIn(TEST_KEY, sysfile.read_text())
        self.assertNotIn(TEST_KEY, ui.all_text())          # Key wird nie angezeigt
        self.assertIn("Verbindung klappt", ui.all_text())
        self.assertEqual(self.opened, [f"https://127.0.0.1:{srv.port}/"])
        self.assertEqual(self.started, [1])                 # Prüfung angestossen
        self.assertEqual(srv.received_methods[0], "auth.login_ex")

    def test_http_adresse_wird_abgelehnt_und_neu_gefragt(self):
        with self.server() as srv:
            script = self.add_script(srv.port)
            script[2:3] = [("ask", "http://127.0.0.1"), ("error", None), ("ask", "127.0.0.1")]
            ui = ScriptedUI(script + [("info", None), ("info", None)])
            self.assistant(ui).run()
        self.assertIn("Unsichere Adresse", ui.all_text())
        self.assertTrue((paths.systems_dir() / "homelab.toml").exists())

    def test_fingerabdruck_nicht_bestaetigt_bricht_ab(self):
        with self.server() as srv:
            ui = ScriptedUI(self.add_script(srv.port)[:5] + [("yesno", False)])
            self.assertEqual(self.assistant(ui).run(), 0)
        self.assertFalse(paths.systems_dir().exists() and any(paths.systems_dir().iterdir()))
        self.assertFalse(paths.keys_dir().exists())
        self.assertEqual(srv.received_methods, [])  # Key wurde nie gesendet

    def test_falscher_key_dann_richtiger(self):
        with self.server() as srv:
            script = self.add_script(srv.port, key="falscher-key")
            script += [("yesno", True), ("secret", TEST_KEY), ("info", None), ("info", None)]
            ui = ScriptedUI(script)
            self.assistant(ui).run()
        self.assertIn("Anmeldung fehlgeschlagen", ui.all_text())
        self.assertEqual((paths.keys_dir() / "homelab").read_text().strip(), TEST_KEY)

    def test_fehlende_rechte_trotzdem_speichern(self):
        with self.server(denied={"alert.list"}) as srv:
            ui = ScriptedUI(self.add_script(srv.port) + [("choose", "save"), ("info", None)])
            self.assistant(ui).run()
        self.assertIn("Readonly Admin", ui.all_text())
        self.assertTrue((paths.systems_dir() / "homelab.toml").exists())

    def flaky_check(self, failures, reason):
        """Prüfung, die zuerst <failures>-mal mit <reason> scheitert, dann echt prüft."""
        seen = []

        def check(cfg, secret):
            seen.append(cfg.timeout_seconds)
            if len(seen) <= failures:
                return checker.offline_entry(cfg, "unreachable", reason)
            return checker.check_system(cfg, secret)
        return check, seen

    def test_zeitueberschreitung_nochmal_testen(self):
        reason = "Nicht erreichbar: Empfangen fehlgeschlagen (Zeitüberschreitung)."
        with self.server() as srv:
            ui = ScriptedUI(self.add_script(srv.port) + [("choose", "retry"), ("info", None), ("info", None)])
            a = self.assistant(ui)
            a.check, seen = self.flaky_check(1, reason)
            a.run()
        text = ui.all_text()
        self.assertIn("nicht rechtzeitig geantwortet", text)
        self.assertIn("Tailscale", text)
        self.assertIn("Nochmal testen", text)
        self.assertIn("Verbindung klappt", text)              # zweiter Versuch klappt
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(t >= setup.TEST_TIMEOUT for t in seen))  # längere Wartezeit beim Test
        cfg = c.load_system(paths.systems_dir() / "homelab.toml")
        self.assertEqual(cfg.timeout_seconds, 20)             # gespeichert wird der normale Wert

    def test_keine_verbindung_trotzdem_speichern(self):
        reason = "Nicht erreichbar: Keine Verbindung zu 127.0.0.1:443 (Zeitüberschreitung)."
        with self.server() as srv:
            ui = ScriptedUI(self.add_script(srv.port) + [("choose", "save"), ("info", None)])
            a = self.assistant(ui)
            a.check, _ = self.flaky_check(5, reason)
            a.run()
        self.assertIn("Es kam gar keine Verbindung zustande", ui.all_text())
        self.assertTrue((paths.systems_dir() / "homelab.toml").exists())

    def test_zeitueberschreitung_abbrechen_speichert_nichts(self):
        reason = "Nicht erreichbar: Empfangen fehlgeschlagen (Zeitüberschreitung)."
        with self.server() as srv:
            ui = ScriptedUI(self.add_script(srv.port) + [("choose", setup.Cancelled())])
            a = self.assistant(ui)
            a.check, _ = self.flaky_check(5, reason)
            a.run()
        self.assertFalse((paths.systems_dir() / "homelab.toml").exists())
        self.assertFalse((paths.keys_dir() / "homelab").exists())

    def test_fingerabdruck_anleitung(self):
        with self.server() as srv:
            ui = ScriptedUI(self.add_script(srv.port)[:5] + [("yesno", False)])
            self.assistant(ui).run()
        text = ui.all_text()
        for needle in ("System → Shell", "openssl x509 -in /etc/certificates/", "Firefox",
                       "Fingerabdrücke", "Chrome", "Details", "JETZT vergleichen, bevor der API-Key gesendet wird",
                       "neu prüfen"):
            self.assertIn(needle, text)

    def test_nicht_erreichbar_abbrechen(self):
        with self.server() as srv:
            port = srv.port
        script = self.add_script(port)[:4] + [("yesno", False)]  # nicht erreichbar -> Abbrechen
        ui = ScriptedUI(script)
        self.assertEqual(self.assistant(ui).run(), 0)
        self.assertIn("nicht erreichbar", ui.all_text())

    def test_kwallet_speicherung_ueber_stdin(self):
        stored = []
        with self.server() as srv:
            script = self.add_script(srv.port)
            script.insert(8, ("choose", "secret-tool"))
            ui = ScriptedUI(script + [("info", None), ("info", None)])
            a = self.assistant(ui, has_secret_tool=True)
            a.store_secret = lambda attrs, label, secret: stored.append((attrs, label, secret.reveal()))
            a.run()
        self.assertEqual(stored, [({"service": "truenas-widget", "account": "homelab"},
                                   "TrueNAS-Widget: homelab", TEST_KEY)])
        self.assertFalse((paths.keys_dir() / "homelab").exists())
        cfg = c.load_system(paths.systems_dir() / "homelab.toml")
        self.assertEqual(cfg.key_source, "secret-tool")

    def make_existing(self, port, fingerprint=None):
        cfg = c.SystemConfig(id="homelab", name="homelab", host="127.0.0.1", port=port,
                             username="widget-leser", fingerprint=fingerprint or self.certs.fingerprint)
        c.write_private(paths.systems_dir() / "homelab.toml", c.system_to_toml(cfg))
        c.write_private(paths.keys_dir() / "homelab", TEST_KEY + "\n")
        return cfg

    def test_entfernen(self):
        self.make_existing(443)
        ui = ScriptedUI([("choose", "del:homelab"), ("yesno", True), ("yesno", True), ("info", None),
                         ("choose", setup.Cancelled())])
        self.assistant(ui).run()
        self.assertFalse((paths.systems_dir() / "homelab.toml").exists())
        self.assertFalse((paths.keys_dir() / "homelab").exists())

    def test_key_erneuern(self):
        with self.server(api_key="neuer-key") as srv:
            self.make_existing(srv.port)
            ui = ScriptedUI([("choose", "key:homelab"), ("secret", "neuer-key"), ("info", None),
                             ("info", None), ("choose", setup.Cancelled())])
            self.assistant(ui).run()
        self.assertEqual((paths.keys_dir() / "homelab").read_text().strip(), "neuer-key")

    def test_fingerabdruck_geaendert(self):
        with self.server() as srv:
            self.make_existing(srv.port, fingerprint="cd" * 32)
            ui = ScriptedUI([("choose", "fp:homelab"), ("yesno", False), ("yesno", True), ("info", None),
                             ("choose", setup.Cancelled())])
            self.assistant(ui).run()
        self.assertIn("GEÄNDERT", ui.all_text())
        self.assertEqual(c.load_system(paths.systems_dir() / "homelab.toml").fingerprint,
                         self.certs.fingerprint)

    def test_umbenennen_behaelt_key(self):
        with self.server() as srv:
            self.make_existing(srv.port)
            ui = ScriptedUI([("choose", "edit:homelab"), ("ask", "Zuhause"), ("ask", None), ("ask", None),
                             ("ask", None), ("info", None), ("choose", setup.Cancelled())])
            self.assistant(ui).run()
        cfg = c.load_system(paths.systems_dir() / "homelab.toml")
        self.assertEqual(cfg.name, "Zuhause")
        self.assertEqual(cfg.id, "homelab")
        self.assertEqual((paths.keys_dir() / "homelab").read_text().strip(), TEST_KEY)


class KDialogTests(unittest.TestCase):
    """Echter Aufruf eines nachgebauten kdialog: Argumente und Rückgabe."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.log = root / "argv"
        self.exe = root / "kdialog"
        # Antwortet auf --password mit dem Key, auf --inputbox mit "eingabe",
        # auf --menu mit dem ersten Eintrag; --yesno: Exit 1 (Nein)
        self.exe.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" >> "{self.log}"\n'
            'case "$*" in\n'
            f'  *--password*) echo "{TEST_KEY}" ;;\n'
            '  *--inputbox*) echo "eingabe" ;;\n'
            '  *--menu*) echo "erster" ;;\n'
            '  *--yesno*) exit 1 ;;\n'
            "esac\n")
        self.exe.chmod(0o755)
        self.ui = setup.KDialogUI(str(self.exe))

    def tearDown(self):
        self.tmp.cleanup()

    def test_key_nur_ueber_ausgabe(self):
        self.assertEqual(self.ui.secret("API-Key einfügen"), TEST_KEY)
        argv = self.log.read_text()
        self.assertIn("--password", argv)
        self.assertNotIn(TEST_KEY, argv)  # nie auf der Befehlszeile

    def test_eingabe_menue_jaNein(self):
        self.assertEqual(self.ui.ask("Name", "vorgabe"), "eingabe")
        self.assertEqual(self.ui.choose("Was?", [("erster", "Eins"), ("zweiter", "Zwei")]), "erster")
        self.assertFalse(self.ui.yesno("Sicher?"))
        argv = self.log.read_text().splitlines()
        self.assertIn("--title", argv)
        self.assertIn(setup.TITLE, argv)

    def test_abbrechen(self):
        self.exe.write_text("#!/bin/sh\nexit 1\n")
        with self.assertRaises(setup.Cancelled):
            self.ui.ask("Name")
        with self.assertRaises(setup.Cancelled):
            self.ui.secret("Key")


if __name__ == "__main__":
    unittest.main()
