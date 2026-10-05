"""Tests: Benachrichtigung genau einmal pro Ereignis."""

import json
import tempfile
import unittest
from pathlib import Path

from truenas_widget import checker, notify

from . import fixtures as fx
from .helpers import make_app, make_cfg


def entry(data, sid="test-nas", name="Test-NAS"):
    raw = {"alerts": data["alert.list"], "apps": data["app.query"],
           "system": data["update.status"], "denied": []}
    return checker.build_entry(make_cfg(system_id=sid, name=name), raw)


def build(data, *more_entries):
    return checker.build_status(make_app(), [entry(data), *more_entries], now=1)


class NotifyOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state" / "notified.json"
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def sender(self, urgency, title, body):
        self.sent.append((urgency, title, body))

    def run_check(self, data, enabled=True, *more_entries):
        return notify.process(build(data, *more_entries), self.state, enabled, sender=self.sender)

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
        offline = checker.build_status(make_app(), [checker.offline_entry(make_cfg(), "unreachable", "weg")])
        notify.process(offline, self.state, True, sender=self.sender)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state.read_text(), before)
        # Nach dem Offline-Intervall: dieselbe Warnung NICHT erneut melden
        self.run_check(fx.scenario(**{"alert.list": [fx.alert("WARNING", uuid="w1")]}))
        self.assertEqual(len(self.sent), 1)

    def test_abgeschaltet_sendet_nichts_merkt_sich_aber_den_stand(self):
        data = fx.scenario(**{"update.status": fx.update_status("25.10.8")})
        self.run_check(data, enabled=False)
        self.assertEqual(self.sent, [])
        self.assertIn("test-nas:25.10.8", json.loads(self.state.read_text())["system"])
        self.run_check(data, enabled=True)  # beim Wieder-Einschalten kein Nachholen alter Meldungen
        self.assertEqual(self.sent, [])

    def test_kaputte_zustandsdatei(self):
        self.state.parent.mkdir(parents=True)
        self.state.write_text("{kaputt")
        self.run_check(fx.scenario(**{"update.status": fx.update_status("25.10.8")}))
        self.assertEqual(len(self.sent), 1)


    def test_zwei_systeme_getrennt_gemerkt(self):
        upd = fx.scenario(**{"app.query": [fx.app("jellyfin", upgrade=True, latest="1.2.4")]})
        remote = entry(upd, "remote", "Remote")
        self.run_check(upd, True, remote)
        self.assertEqual(len(self.sent), 2)  # gleiche App auf zwei Systemen = zwei Meldungen
        self.assertEqual({t for _, t, _ in self.sent},
                         {"Test-NAS: App-Updates verfügbar", "Remote: App-Updates verfügbar"})
        self.run_check(upd, True, remote)
        self.assertEqual(len(self.sent), 2)

    def test_offline_system_vergisst_nichts(self):
        upd = fx.scenario(**{"app.query": [fx.app("jellyfin", upgrade=True, latest="1.2.4")]})
        self.run_check(upd, True, entry(upd, "remote", "Remote"))
        self.assertEqual(len(self.sent), 2)
        down = checker.offline_entry(make_cfg(system_id="remote", name="Remote"), "unreachable", "weg")
        self.run_check(upd, True, down)          # remote kurz weg
        self.run_check(upd, True, entry(upd, "remote", "Remote"))  # wieder da
        self.assertEqual(len(self.sent), 2, "Nach Rückkehr darf nichts erneut gemeldet werden")


if __name__ == "__main__":
    unittest.main()
