import json
import socket
import struct
import tempfile
import time
import os
import unittest

from race_engineer.live import LiveFeed, decode
from race_engineer.send_raw import FRAME, frames
from race_engineer.state import LiveState


class StubParser:
    """Datagram = JSON {"h": header, "b": body}. Mirrors the parse_header/parse_packet API
    that udp_capture.py uses."""

    def parse_header(self, data):
        return json.loads(data)["h"]

    def parse_packet(self, data):
        return {"header": json.loads(data)["h"], "body": json.loads(data)["b"]}


def dgram(name, uid, body, pid=0):
    h = {"packet_name": name, "m_packetId": pid, "m_sessionTime": 1.5,
         "m_frameIdentifier": 7, "m_sessionUID": uid}
    return json.dumps({"h": h, "b": body}).encode()


PARTS = {"packet_name": "Participants", "body": {"numActiveCars": 1,
         "participants": [{"aiControlled": 0, "name": "NORRIS", "raceNumber": 4}]}}


def wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.state = LiveState()
        self.feed = LiveFeed(self.state, StubParser(), port=0).start()
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def tearDown(self):
        self.feed.stop()
        self.tx.close()

    def send(self, data):
        self.tx.sendto(data, ("127.0.0.1", self.feed.port))

    def test_decode_shape_matches_capture_lines(self):
        h, pkt = decode(StubParser(), dgram("Participants", 5, PARTS["body"], 4))
        self.assertEqual(pkt["packet_name"], "Participants")
        self.assertEqual(pkt["sessionTime"], 1.5)
        self.assertEqual(pkt["participants"][0]["name"], "NORRIS")

    def test_unwanted_packets_not_decoded(self):
        h, pkt = decode(StubParser(), dgram("SessionHistory", 5, {}))
        self.assertIsNone(pkt)

    def test_feeds_state_over_udp(self):
        self.send(dgram("Participants", 5, PARTS["body"], 4))
        self.assertTrue(wait(lambda: self.state.participants))
        self.assertEqual(self.state.player_slot(), 0)

    def test_new_session_uid_resets_state(self):
        self.send(dgram("Participants", 5, PARTS["body"], 4))
        self.assertTrue(wait(lambda: self.state.participants))
        self.send(dgram("Session", 6, {"weather": 3}, 1))
        self.assertTrue(wait(lambda: self.state.session is not None))
        self.assertEqual(self.state.participants, [])  # old session's data was cleared

    def test_zero_uid_does_not_reset(self):
        self.send(dgram("Participants", 5, PARTS["body"], 4))
        self.assertTrue(wait(lambda: self.state.participants))
        self.send(dgram("Session", 0, {"weather": 3}, 1))
        self.assertTrue(wait(lambda: self.state.session is not None))
        self.assertTrue(self.state.participants)

    def test_bad_datagram_counted_not_fatal(self):
        self.send(b"not json")
        self.assertTrue(wait(lambda: self.feed.errors == 1))
        self.send(dgram("Participants", 5, PARTS["body"], 4))
        self.assertTrue(wait(lambda: self.state.participants))

    def test_raw_bin_frames(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "raw.bin")
            with open(p, "wb") as f:
                for i, payload in enumerate((b"abc", b"defgh")):
                    f.write(struct.pack("<dI", 100.0 + i, len(payload)) + payload)
                f.write(FRAME.pack(200.0, 50) + b"short")  # truncated final frame is ignored
            self.assertEqual([x[1] for x in frames(p)], [b"abc", b"defgh"])


if __name__ == "__main__":
    unittest.main()
