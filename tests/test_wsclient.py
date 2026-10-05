"""Tests: WebSocket-Rahmen kodieren/dekodieren (klein, mittel, gross)."""

import os
import shutil
import ssl
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

from truenas_widget import wsclient as w


class FrameTests(unittest.TestCase):
    def roundtrip(self, size, mask):
        payload = bytes(i % 251 for i in range(size))
        frame = w.encode_frame(w.OP_TEXT, payload, mask=mask)
        fin, opcode, data, used = w.decode_frame(frame)
        self.assertTrue(fin)
        self.assertEqual(opcode, w.OP_TEXT)
        self.assertEqual(data, payload)
        self.assertEqual(used, len(frame))
        return frame

    def test_groessen(self):
        for size in (0, 1, 125, 126, 65535, 65536, 200_000):
            for mask in (True, False):
                with self.subTest(size=size, mask=mask):
                    self.roundtrip(size, mask)

    def test_client_maskiert(self):
        frame = self.roundtrip(10, True)
        self.assertTrue(frame[1] & 0x80, "Client-Rahmen müssen maskiert sein")


@unittest.skipUnless(shutil.which("openssl"), "openssl fehlt")
class CertExpiryTests(unittest.TestCase):
    """"Gültig bis" aus dem Zertifikat lesen - Vergleich mit openssl."""

    def check_days(self, days):
        with tempfile.TemporaryDirectory() as tmp:
            cert = os.path.join(tmp, "c.pem")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", os.path.join(tmp, "k.pem"), "-out", cert, "-days", str(days),
                            "-subj", "/CN=test"], check=True, capture_output=True)
            end = subprocess.run(["openssl", "x509", "-in", cert, "-noout", "-enddate"],
                                 check=True, capture_output=True, text=True).stdout.strip()
            with open(cert) as fh:
                der = ssl.PEM_cert_to_DER_cert(fh.read())
        expected = datetime.strptime(end.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        self.assertEqual(w.cert_not_after(der), expected)

    def test_kurz(self):
        self.check_days(1)

    def test_ein_jahr(self):
        self.check_days(400)

    def test_nach_2050_anderes_datumsformat(self):
        self.check_days(10000)  # GeneralizedTime statt UTCTime

    def test_unsinn_ergibt_none(self):
        self.assertIsNone(w.cert_not_after(b""))
        self.assertIsNone(w.cert_not_after(b"\x30\x03\x02\x01\x01"))


if __name__ == "__main__":
    unittest.main()
