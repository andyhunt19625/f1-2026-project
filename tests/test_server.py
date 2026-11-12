import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from race_engineer import ask, control
from race_engineer.live import LiveFeed
from race_engineer.server import make_server
from race_engineer.state import LiveState


def lap(pos, lapnum, leader_ms, front_ms):
    return {"carPosition": pos, "currentLapNum": lapnum, "numPitStops": 0,
            "deltaToRaceLeaderMSPart": leader_ms % 60000, "deltaToRaceLeaderMinutesPart": leader_ms // 60000,
            "deltaToCarInFrontMSPart": front_ms % 60000, "deltaToCarInFrontMinutesPart": front_ms // 60000,
            "pitLaneTimerActive": 0, "pitLaneTimeInLaneInMS": 0}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.state = LiveState()
        self.state.ingest({"packet_name": "Participants", "numActiveCars": 3, "participants": [
            {"aiControlled": 0, "name": "NORRIS", "raceNumber": 4},
            {"aiControlled": 1, "name": "HAMILTON", "raceNumber": 44},
            {"aiControlled": 1, "name": "PÉREZ", "raceNumber": 11}]})
        self.state.ingest({"packet_name": "LapData", "sessionTime": 12.5, "lapData": [
            lap(1, 3, 0, 0), lap(2, 3, 30000, 30000), lap(3, 3, 40000, 10000)]})
        self.httpd = make_server(self.state, None, "127.0.0.1", 0, {"pit_loss": 26.0, "buffer": 5.0})
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path):
        return ask.fetch(path, "127.0.0.1", self.port)

    def test_status(self):
        code, d = self.get("/status")
        self.assertEqual(code, 200)
        self.assertTrue(d["ready"])
        self.assertEqual(d["you"], "NORRIS")

    def test_gap_by_name(self):
        code, d = self.get(ask.build_path(["gap", "lewis", "hamilton"]))
        self.assertEqual(code, 200)
        self.assertAlmostEqual(d["gap_s"], 30.0)

    def test_pit_uses_server_defaults_and_overrides(self):
        code, d = self.get(ask.build_path(["pit"]))
        self.assertEqual(code, 200)
        self.assertAlmostEqual(d["rejoin_clear_s"], 4.0)     # 30 - 26 (the brief's example)
        self.assertFalse(d["in_window"])
        code, d = self.get(ask.build_path(["pit", "20", "2"]))
        self.assertTrue(d["in_window"])

    def test_errors_are_400_not_crashes(self):
        code, d = self.get(ask.build_path(["gap", "alonso"]))
        self.assertEqual(code, 400)
        self.assertIn("error", d)
        code, d = self.get("/ask?tool=pit_window&pit_loss=abc")
        self.assertEqual(code, 400)
        code, d = self.get("/ask?tool=nope")
        self.assertEqual(code, 400)
        code, d = self.get("/nothing")
        self.assertEqual(code, 404)

    def test_ask_when_not_running(self):
        code, d = ask.fetch("/status", "127.0.0.1", 1)   # nothing listens on port 1
        self.assertIsNone(code)
        self.assertIn("not running", d["error"])


class LiveBindTests(unittest.TestCase):
    def test_second_listener_on_same_port_fails_loudly(self):
        class P:  # parser is not used for binding
            pass
        feed = LiveFeed(LiveState(), P(), port=0)
        try:
            with self.assertRaises(OSError):
                LiveFeed(LiveState(), P(), port=feed.port)
        finally:
            feed.sock.close()


class ControlTests(unittest.TestCase):
    def test_session_in_progress_detects_recent_writes(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "20261112T150000Z_abc"
            f.mkdir()
            p = f / "parsed.jsonl"
            p.write_text("x")
            now = time.time()
            self.assertTrue(control.session_in_progress(d, now))
            os.utime(p, (now - 300, now - 300))
            self.assertFalse(control.session_in_progress(d, now))

    def test_session_in_progress_missing_folder(self):
        self.assertFalse(control.session_in_progress("/no/such/folder"))

    def test_listening_parses_ss_output(self):
        class R:
            stdout = ("UNCONN 0      0            0.0.0.0:20777      0.0.0.0:*\n"
                      "UNCONN 0      0            0.0.0.0:5353       0.0.0.0:*\n")
        ports = control.listening(runner=lambda cmd: R())
        self.assertEqual(ports, {20777: True, 20778: False})

    def test_capture_commands_are_exactly_the_sudoers_ones(self):
        seen = []

        def fake(cmd):
            seen.append(cmd)

            class R:
                returncode = 0
                stdout = "active\n"
                stderr = ""
            return R()
        control.capture_state(fake)
        control.start_capture(fake)
        control.stop_capture(fake)
        self.assertEqual(seen[0], ["systemctl", "is-active", "f1-esports-capture.service"])
        self.assertEqual(seen[1], ["sudo", "-n", "systemctl", "start", "f1-esports-capture.service"])
        self.assertEqual(seen[2], ["sudo", "-n", "systemctl", "stop", "f1-esports-capture.service"])


if __name__ == "__main__":
    unittest.main()
