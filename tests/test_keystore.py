"""Tests: API-Key lesen, Rechte-Warnung, Key taucht nie in Ausgaben auf."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from truenas_widget import keystore

from .helpers import KeyLeakTestCase
from .mock_truenas import TEST_KEY


class KeyFileTests(KeyLeakTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "truenas-widget"
        self.dir.mkdir(mode=0o700)
        os.chmod(self.dir, 0o700)
        self.keyfile = self.dir / "api-key"
        self.keyfile.write_text(TEST_KEY + "\n")

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_rechte_600_ohne_warnung(self):
        os.chmod(self.keyfile, 0o600)
        secret = keystore.read_key_file(self.keyfile)
        self.assertEqual(secret.reveal(), TEST_KEY)
        self.assertNotIn("WARNING", self.log_text())

    @unittest.skipIf(os.name != "posix", "nur unter Linux")
    def test_zu_offene_rechte_warnen(self):
        os.chmod(self.keyfile, 0o644)
        with self.assertLogs("truenas_widget.keystore", level="WARNING") as cm:
            secret = keystore.read_key_file(self.keyfile)
        self.assertEqual(secret.reveal(), TEST_KEY)  # Key wird trotzdem gelesen ...
        text = "\n".join(cm.output)
        self.assertIn("zu offene Rechte", text)    # ... aber es wird gewarnt
        self.assertIn("chmod 600", text)
        self.assertNotIn(TEST_KEY, text)

    def test_offener_ordner_warnt(self):
        os.chmod(self.keyfile, 0o600)
        os.chmod(self.dir, 0o755)
        warnings = keystore.check_permissions(self.keyfile)
        self.assertTrue(any("chmod 700" in w for w in warnings))

    def test_fehlende_datei(self):
        with self.assertRaises(keystore.KeyError_) as ctx:
            keystore.read_key_file(self.dir / "gibtsnicht")
        self.assertNotIn(TEST_KEY, str(ctx.exception))

    def test_leere_datei(self):
        self.keyfile.write_text("\n")
        with self.assertRaises(keystore.KeyError_):
            keystore.read_key_file(self.keyfile)


class SecretTests(KeyLeakTestCase):
    def test_secret_wird_nie_ausgegeben(self):
        s = keystore.Secret(TEST_KEY)
        for text in (str(s), repr(s), f"{s}", "%s" % s, str([s]), str({"k": s})):
            self.assertNotIn(TEST_KEY, text)
        self.assertEqual(s.redact(f"x {TEST_KEY} y"), "x *** y")

    def test_secret_tool_lesen(self):
        fake = mock.Mock(returncode=0, stdout=TEST_KEY + "\n", stderr="")
        with mock.patch("subprocess.run", return_value=fake) as run:
            s = keystore.read_secret_tool({"service": "truenas-widget", "account": "api-key"})
        self.assertEqual(s.reveal(), TEST_KEY)
        self.assertEqual(run.call_args[0][0],
                         ["secret-tool", "lookup", "service", "truenas-widget", "account", "api-key"])

    def test_secret_tool_fehlt(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(keystore.KeyError_) as ctx:
                keystore.read_secret_tool({"service": "x"})
        self.assertIn("libsecret", str(ctx.exception))

    def test_secret_tool_ohne_eintrag(self):
        fake = mock.Mock(returncode=1, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=fake):
            with self.assertRaises(keystore.KeyError_):
                keystore.read_secret_tool({"service": "x"})


if __name__ == "__main__":
    unittest.main()
