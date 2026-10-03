"""Tests: Benachrichtigung genau einmal pro Ereignis."""

import json
import tempfile
import unittest
from pathlib import Path

from truenas_widget import checker, notify

from . import fixtures as fx
from .helpers import make_cfg


def build(data):
    raw = {"alerts": data["alert.list"], "apps": data["app.query"],
           "system": data["update.status"], "denied": []}
    return checker.build_status(make_cfg(), raw, now=1)


class NotifyOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state" / "notified.json"
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def sender(self, urgency, title, body):
        self.sent.append((urgency, title, body))

    def run_check(self, data, enabled=True):
        return notify.process(build(data), self.state, enabled, sender=self.sender)

    def test_benachrichtigung_nur_einmal(self):
        data = fx.scenario(**{
            "alert.list": [fx.alert("WARNING", uuid="w1")],
            "app.query": [fx.app("jellyfin", upgrade=True, latest="1.2.4")],
            "update.status": fx.update_status("25.10.8"),
        })
        self.run_check(data)
        self.assertEqual(len(self.sent), 3)  # Warnung, App-Update, Systemupdate
        self.run_check(data)
        self.run_check(data)
        self.assertEqual(len(self.sent), 3, "Gleiche Ereignisse dürfen nicht erneut gemeldet werden")

    def test_neue_app_version_wird_erneut_gemeldet(self):
        self.run_check(fx.scenario(**{"app.query": [fx.app("jellyfin", upgrade=True, latest="1.2.4")]}))
        self.run_check(fx.scenario(**{"app.query": [fx.app("jellyfin", upgrade=True, latest="1.2.5")]}))
        self.assertEqual(len(self.sent), 2)
        self.assertIn("1.2.5", self.sent[1][2])

    def test_neuer_alert_wird_gemeldet_alter_nicht(self):
        self.run_check(fx.scenario(**{"alert.list": [fx.alert("WARNING", uuid="w1")]}))
        self.run_check(fx.scenario(**{"alert.list": [fx.alert("WARNING", uuid="w1"),
                                                     fx.alert("CRITICAL", uuid="c1")]}))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[1][0], "critical")
        self.assertIn("CRITICAL", self.sent[1][2])
        self.assertNotIn("WARNING", self.sent[1][2])

    def test_info_und_quittiert_loesen_nichts_aus(self):
        self.run_check(fx.scenario(**{"alert.list": [fx.alert("INFO"), fx.alert("ERROR", dismissed=True)]}))
        self.assertEqual(self.sent, [])

    def test_offline_keine_benachrichtigung_und_zustand_bleibt(self):
        self.run_check(fx.scenario(**{"alert.list": [fx.alert("WARNING", uuid="w1")]}))
        before = self.state.read_text()
        notify.process(checker.offline_status(make_cfg(), "weg"), self.state, True, sender=self.sender)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state.read_text(), before)
        # Nach dem Offline-Intervall: dieselbe Warnung NICHT erneut melden
        self.run_check(fx.scenario(**{"alert.list": [fx.alert("WARNING", uuid="w1")]}))
        self.assertEqual(len(self.sent), 1)

    def test_abgeschaltet_sendet_nichts_merkt_sich_aber_den_stand(self):
        data = fx.scenario(**{"update.status": fx.update_status("25.10.8")})
        self.run_check(data, enabled=False)
        self.assertEqual(self.sent, [])
        self.assertIn("25.10.8", json.loads(self.state.read_text())["system"])
        self.run_check(data, enabled=True)  # beim Wieder-Einschalten kein Nachholen alter Meldungen
        self.assertEqual(self.sent, [])

    def test_kaputte_zustandsdatei(self):
        self.state.parent.mkdir(parents=True)
        self.state.write_text("{kaputt")
        self.run_check(fx.scenario(**{"update.status": fx.update_status("25.10.8")}))
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
