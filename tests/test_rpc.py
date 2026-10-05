# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests: Whitelist verweigert Schreibmethoden, Fehler-Zuordnung, Anmeldung."""

import json
import unittest

from truenas_widget import keystore, rpc

from .helpers import KeyLeakTestCase
from .mock_truenas import TEST_KEY


class FakeTransport:
    """Simuliert die Verbindung: merkt sich Gesendetes, liefert vorbereitete Antworten."""

    def __init__(self, replies=None):
        self.sent = []
        self.replies = list(replies or [])

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def recv_text(self):
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(self.sent[-1])
        return json.dumps(reply)

    def close(self):
        pass


def ok(result):
    return lambda req: {"jsonrpc": "2.0", "id": req["id"], "result": result}


class WhitelistTests(KeyLeakTestCase):
    FORBIDDEN = ["app.upgrade", "app.start", "app.stop", "app.redeploy", "app.delete",
                 "update.run", "update.download", "update.update", "system.reboot",
                 "system.shutdown", "alert.dismiss", "service.restart", "pool.scrub.run",
                 "core.call_hook", "filesystem.put", "user.update", "api_key.create"]

    def test_schreibmethoden_werden_verweigert_und_nicht_gesendet(self):
        t = FakeTransport()
        client = rpc.Client(t)
        for method in self.FORBIDDEN:
            with self.subTest(method=method):
                with self.assertRaises(rpc.MethodNotAllowed):
                    client.call(method)
        self.assertEqual(t.sent, [], "Es darf nichts gesendet worden sein")

    def test_whitelist_ist_genau_festgelegt(self):
        self.assertEqual(rpc.ALLOWED_METHODS,
                         {"auth.login_ex", "alert.list", "app.query", "update.status"})
        self.assertIsInstance(rpc.ALLOWED_METHODS, frozenset)  # zur Laufzeit unveränderbar

    def test_erlaubte_methode(self):
        t = FakeTransport([ok([1, 2])])
        self.assertEqual(rpc.Client(t).call("alert.list"), [1, 2])
        self.assertEqual(t.sent[0]["jsonrpc"], "2.0")
        self.assertEqual(t.sent[0]["method"], "alert.list")

    def test_fremde_nachrichten_werden_uebersprungen(self):
        t = FakeTransport([{"jsonrpc": "2.0", "method": "collection_update", "params": {}},
                           {"jsonrpc": "2.0", "id": 999, "result": "falsch"}, ok("richtig")])
        self.assertEqual(rpc.Client(t).call("update.status"), "richtig")

    def test_zugriff_verweigert(self):
        t = FakeTransport([lambda req: {"jsonrpc": "2.0", "id": req["id"], "error": {
            "code": -32001, "message": "Method call error",
            "data": {"error": 13, "errname": "EACCES", "reason": "Not authorized"}}}])
        with self.assertRaises(rpc.RPCError) as ctx:
            rpc.Client(t).call("app.query")
        self.assertTrue(ctx.exception.access_denied)


class LoginTests(KeyLeakTestCase):
    def test_login_erfolgreich(self):
        t = FakeTransport([ok({"response_type": "SUCCESS"})])
        rpc.Client(t).login("widget-leser", keystore.Secret(TEST_KEY))
        sent = t.sent[0]
        self.assertEqual(sent["method"], "auth.login_ex")
        self.assertEqual(sent["params"][0]["mechanism"], "API_KEY_PLAIN")

    def test_login_falscher_key(self):
        t = FakeTransport([ok({"response_type": "AUTH_ERR"})])
        with self.assertRaises(rpc.AuthFailed) as ctx:
            rpc.Client(t).login("widget-leser", keystore.Secret(TEST_KEY))
        self.assertNotIn(TEST_KEY, str(ctx.exception))

    def test_key_wird_aus_fehlermeldung_entfernt(self):
        # Selbst wenn der Server den Key in der Fehlermeldung zurückschicken würde:
        t = FakeTransport([lambda req: {"jsonrpc": "2.0", "id": req["id"], "error": {
            "code": -32602, "message": f"Invalid params {TEST_KEY}",
            "data": {"errname": "EINVAL", "reason": f"bad {TEST_KEY}"}}}])
        with self.assertRaises(rpc.RPCError) as ctx:
            rpc.Client(t).login("widget-leser", keystore.Secret(TEST_KEY))
        self.assertNotIn(TEST_KEY, str(ctx.exception))
        self.assertNotIn(TEST_KEY, ctx.exception.reason)


if __name__ == "__main__":
    unittest.main()
