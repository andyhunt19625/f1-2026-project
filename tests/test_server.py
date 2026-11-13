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

    def test_damage(self):
        dmg = {"tyresWear": [1, 1, 1, 1], "tyresDamage": [1, 2, 3, 4], "brakesDamage": [0, 0, 0, 0],
               "frontLeftWingDamage": 15, "frontRightWingDamage": 0, "rearWingDamage": 0,
               "floorDamage": 8, "diffuserDamage": 0, "sidepodDamage": 0, "drsFault": 0,
               "ersFault": 0, "gearBoxDamage": 17, "engineDamage": 30, "engineBlown": 0,
               "engineSeized": 0}
        self.state.ingest({"packet_name": "CarDamage", "carDamageData": [dmg, dict(dmg), dict(dmg)]})
        code, d = self.get(ask.build_path(["damage"]))
        self.assertEqual(code, 200)
        self.assertEqual(d["answer"], "You have damage: front-left wing 15%, floor 8%.")
        self.assertEqual(d["parts_pct"]["engine"], 30)
        self.assertEqual(d["faults"], [])
        clean = dict(dmg, frontLeftWingDamage=0, floorDamage=0, drsFault=1)
        self.state.ingest({"packet_name": "CarDamage", "carDamageData": [clean, dmg, dmg]})
        code, d = self.get(ask.build_path(["damage"]))
        self.assertEqual(d["answer"], "You have no wing, floor, diffuser or sidepod damage. Faults: DRS fault.")
        code, d = self.get(ask.build_path(["damage", "hamilton"]))
        self.assertTrue(d["answer"].startswith("Hamilton has damage"))

    def test_ask_when_not_running(self):
        code, d = ask.fetch("/status", "127.0.0.1", 1)   # nothing listens on port 1
        self.assertIsNone(code)
        self.assertIn("not running", d["error"])


class StopFileTests(unittest.TestCase):
    def test_stop_file_triggers_one_clean_stop_and_is_removed(self):
        from race_engineer.server import watch_stop_file
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "engineer.stop"
            hit, stopped = threading.Event(), threading.Event()
            watch_stop_file(f, hit.set, stopped, interval=0.05)
            time.sleep(0.15)
            self.assertFalse(hit.is_set())
            f.write_text("stop")
            self.assertTrue(hit.wait(2))
            time.sleep(0.1)
            self.assertFalse(f.exists())
            stopped.set()


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
    def test_windows_start_capture_never_runs_in_tests_without_the_script(self):
        # Safety net: with the Windows path forced and no capture script present, nothing is spawned.
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(control, "IS_WINDOWS", True), \
             mock.patch.object(control, "CAPTURE_DIR", Path(d)), \
             mock.patch.object(control, "_spawn") as spawn:
            ok, msg = control.start_capture()
            self.assertFalse(ok)
            self.assertIn("not found", msg)
            spawn.assert_not_called()

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
        ports = control.listening(runner=lambda cmd: R(), windows=False)
        self.assertEqual(ports, {20777: True, 20778: False})

    def test_listening_parses_windows_netstat(self):
        class R:
            stdout = ("\nActive Connections\n\n  Proto  Local Address          Foreign Address        State           PID\n"
                      "  UDP    127.0.0.1:20777        *:*                                    4412\n"
                      "  UDP    0.0.0.0:5353           *:*                                    2100\n")
        ports = control.listening(runner=lambda cmd: R(), windows=True)
        self.assertEqual(ports, {20777: True, 20778: False})

    def test_pid_running_checks_marker_and_clears_stale_file(self):
        with tempfile.TemporaryDirectory() as d:
            pf = Path(d) / "x.pid"
            pf.write_text(str(os.getpid()))
            # this test process is python running unittest, so the marker is found on Linux
            if not control.IS_WINDOWS:
                self.assertEqual(control._pid_running(pf, "python"), os.getpid())
                self.assertIsNone(control._pid_running(pf, "no-such-marker"))
                self.assertFalse(pf.exists())   # stale pid file removed

    def test_save_without_drive_keeps_archive_only(self):
        from race_engineer import archive
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = root / "sessions" / "20261112T162447Z_abc"
            s.mkdir(parents=True)
            (s / "raw.bin").write_bytes(b"x" * 100)
            old = time.time() - 600
            os.utime(s / "raw.bin", (old, old))
            res = archive.save([s.name], "t", root / "sessions", root / "archive", None, log=lambda *_: None)
            self.assertIsNone(res["drive_archive"])
            self.assertTrue(Path(res["archive"]).is_file())

    def test_capture_commands_are_exactly_the_sudoers_ones(self):
        # Linux (server) path only. Force it, so on Windows this test can never start a real
        # capture process (it did once on the PC before this guard).
        from unittest import mock
        patcher = mock.patch.object(control, "IS_WINDOWS", False)
        patcher.start()
        self.addCleanup(patcher.stop)
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
