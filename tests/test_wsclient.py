"""Tests: WebSocket-Rahmen kodieren/dekodieren (klein, mittel, gross)."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
