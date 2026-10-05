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



class PersistentProblemTests(unittest.TestCase):
    """Probleme, die nicht von selbst verschwinden: genau einmal melden."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "notified.json"
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_entries(self, *entries, enabled=True):
        status = checker.build_status(make_app(), list(entries), now=1)
        notify.process(status, self.state, enabled, sender=lambda *m: self.sent.append(m))

    def offline(self, kind, sid="remote", reason="Anmeldung fehlgeschlagen (EXPIRED): abgelaufen"):
        return checker.offline_entry(make_cfg(system_id=sid, name=sid), kind, reason)

    def ok(self, sid="remote", cert_days=None, expires="2026-12-31"):
        """System ok; Zertifikat läuft am festen Datum <expires> ab, "jetzt" liegt
        <cert_days> Tage davor (so wie in Wirklichkeit die Zeit vergeht)."""
        raw = {"alerts": [], "apps": [], "system": fx.update_status(), "denied": []}
        e = checker.build_entry(make_cfg(system_id=sid, name=sid), raw)
        if cert_days is not None:
            from datetime import datetime, timedelta, timezone
            not_after = datetime.fromisoformat(expires).replace(tzinfo=timezone.utc)
            checker.apply_cert_expiry(e, not_after, not_after - timedelta(days=cert_days, hours=-1))
        return e

    def titles(self):
        return [t for _, t, _ in self.sent]

    def test_key_abgelaufen_einmal(self):
        for _ in range(3):
            self.run_entries(self.offline("auth"))
        self.assertEqual(self.titles(), ["remote: Anmeldung fehlgeschlagen"])
        self.assertIn("API-Key erneuern", self.sent[0][2])

    def test_wieder_ok_dann_erneut_problem_meldet_neu(self):
        self.run_entries(self.offline("auth"))
        self.run_entries(self.ok())
        self.run_entries(self.offline("auth"))
        self.assertEqual(len(self.sent), 2)

    def test_zwischendurch_nicht_erreichbar_meldet_nicht_doppelt(self):
        self.run_entries(self.offline("auth"))
        self.run_entries(self.offline("unreachable", reason="Nicht erreichbar"))
        self.run_entries(self.offline("auth"))
        self.assertEqual(len(self.sent), 1)

    def test_nicht_erreichbar_und_fingerabdruck_melden_nichts(self):
        for kind in ("unreachable", "fingerprint", "error"):
            self.run_entries(self.offline(kind, reason="x"))
        self.assertEqual(self.sent, [])

    def test_key_nicht_lesbar_und_konfigurationsfehler(self):
        self.run_entries(self.offline("key", "a", "API-Key nicht lesbar"),
                         self.offline("config", "b", "Konfigurationsfehler: host fehlt"))
        self.assertEqual(sorted(self.titles()), ["a: API-Key nicht lesbar", "b: Konfigurationsfehler"])

    def test_fehlende_rechte_einmal(self):
        raw = {"alerts": None, "apps": [], "system": fx.update_status(), "denied": [("alerts", "alert.list")]}
        e = checker.build_entry(make_cfg(system_id="remote", name="remote"), raw)
        self.run_entries(e)
        self.run_entries(e)
        self.assertEqual(self.titles(), ["remote: Zugriff verweigert"])
        self.assertIn("Readonly Admin", self.sent[0][2])

    def test_zertifikat_bald_dann_abgelaufen(self):
        self.run_entries(self.ok(cert_days=200))   # noch lange gültig: nichts
        self.assertEqual(self.sent, [])
        self.run_entries(self.ok(cert_days=20))
        self.run_entries(self.ok(cert_days=19))
        self.assertEqual(self.titles(), ["remote: Zertifikat läuft bald ab"])
        self.run_entries(self.ok(cert_days=-1))
        self.assertEqual(self.titles(), ["remote: Zertifikat läuft bald ab", "remote: Zertifikat abgelaufen"])
        # Zertifikat erneuert (neues Datum, lange gültig): Hinweis weg; später wieder einmal
        self.run_entries(self.ok(cert_days=365, expires="2027-12-31"))
        self.run_entries(self.ok(cert_days=10, expires="2027-12-31"))
        self.assertEqual(len(self.sent), 3)

    def test_abgeschaltet(self):
        self.run_entries(self.offline("auth"), enabled=False)
        self.run_entries(self.offline("auth"))
        self.assertEqual(self.sent, [])  # gemerkt, aber nicht gemeldet und nicht nachgeholt


if __name__ == "__main__":
    unittest.main()
