"""Tests: systemd-Timer (OnCalendar + Persistent=true) und install.sh.

install.sh läuft hier in einer Sandbox: eigenes HOME, und "systemctl" sowie
"kpackagetool6" sind Attrappen, die nur ihre Aufrufe mitschreiben.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from truenas_widget import config as c

REPO = Path(__file__).parent.parent


class TimerFileTests(unittest.TestCase):
    def test_timer_hat_persistent_und_oncalendar(self):
        text = (REPO / "systemd" / "truenas-widget.timer").read_text()
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
        self.assertIn("Persistent=true", lines)
        self.assertIn("OnCalendar=@ONCALENDAR@", lines)
        # Persistent= wirkt nur mit OnCalendar=; monotone Timer wären wirkungslos.
        self.assertFalse(any(l.startswith(("OnUnitActiveSec", "OnActiveSec", "OnStartupSec")) for l in lines))

    def test_oncalendar_ausdruecke(self):
        self.assertEqual(c.oncalendar(15), "*-*-* *:00/15:00")
        self.assertEqual(c.oncalendar(60), "*-*-* 00/1:00:00")
        self.assertEqual(c.oncalendar(360), "*-*-* 00/6:00:00")
        self.assertEqual(c.oncalendar(1440), "*-*-* 00:00:00")

    def test_unpassendes_intervall_wird_abgelehnt(self):
        for bad in (0, 7, 45, 90, 1441):
            with self.subTest(bad=bad):
                with self.assertRaises(c.ConfigError) as ctx:
                    c.global_from_dict({"checker": {"interval_minutes": bad}})
                self.assertIn("Erlaubt sind", str(ctx.exception))

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze nicht vorhanden")
    def test_systemd_versteht_alle_ausdruecke(self):
        for minutes in c.ALLOWED_INTERVALS:
            expr = c.oncalendar(minutes)
            with self.subTest(minutes=minutes):
                proc = subprocess.run(["systemd-analyze", "calendar", "--iterations=3", expr],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)


class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.bin = root / "bin"
        self.calls = root / "calls"
        self.home.mkdir()
        self.bin.mkdir()
        for tool in ("systemctl", "kpackagetool6"):
            f = self.bin / tool
            f.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{self.calls}"\n')
            f.chmod(0o755)
        # install.sh verweigert root; für den Test die Prüfung neutralisieren.
        self.script = root / "install.sh"
        self.script.write_text((REPO / "install.sh").read_text()
                               .replace('if [ "$(id -u)" -eq 0 ]; then', "if false; then")
                               .replace('REPO=$(cd "$(dirname "$0")" && pwd)', f'REPO="{REPO}"'))

    def tearDown(self):
        self.tmp.cleanup()

    def run_install(self):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}"}
        return subprocess.run(["sh", str(self.script)], env=env, capture_output=True, text=True)

    def test_install_richtet_timer_mit_enable_now_ein(self):
        proc = self.run_install()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self.calls.read_text().splitlines()
        self.assertIn("systemctl --user enable --now truenas-widget.timer", calls)
        self.assertIn("systemctl --user start --no-block truenas-widget.service", calls)
        timer = (self.home / ".config/systemd/user/truenas-widget.timer").read_text()
        self.assertIn("OnCalendar=*-*-* *:00/15:00", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIsNone(re.search(r"@[A-Z]+@", timer), "Platzhalter nicht ersetzt")
        service = (self.home / ".config/systemd/user/truenas-widget.service").read_text()
        self.assertIsNone(re.search(r"@[A-Z]+@", service), "Platzhalter nicht ersetzt")

    def test_intervall_aus_konfiguration(self):
        cdir = self.home / ".config" / "truenas-widget"
        cdir.mkdir(parents=True)
        (cdir / "config.toml").write_text('[checker]\ninterval_minutes = 30\n')
        proc = self.run_install()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        timer = (self.home / ".config/systemd/user/truenas-widget.timer").read_text()
        self.assertIn("OnCalendar=*-*-* *:00/30:00", timer)


if __name__ == "__main__":
    unittest.main()
