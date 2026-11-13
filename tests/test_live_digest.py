import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from race_engineer import auto_report, session_report
from race_engineer.state import LiveState
from tests.test_session_report import make_session

UID = 0x8385207b9f1f036b


def drive(folder, digest_dir, end_with_send=True):
    """Feed a recorded test session through the live engineer, as the live feed would."""
    s = LiveState(lap_log=Path(digest_dir) / "laps.txt")
    s.live_laps.digest_dir = digest_dir
    s.set_uid(UID)
    with open(Path(folder) / "parsed.jsonl") as f:
        for line in f:
            line = line.strip("\x00\n")
            if not line:
                continue                     # size padding added by the test
            pkt = json.loads(line)
            for ld in pkt.get("lapData", []):
                ld.update(numPitStops=0, pitLaneTimerActive=0, pitLaneTimeInLaneInMS=0, carPosition=1)
            s.ingest(pkt)
    if end_with_send:
        s.ingest({"packet_name": "Event", "eventCode": "SEND", "details": {}})
    else:
        s.end_session()          # what the live feed does when the next session's UID arrives
    return s


class LiveDigestTests(unittest.TestCase):
    def test_report_from_live_digest_matches_file_read(self):
        with tempfile.TemporaryDirectory() as d:
            sessions = Path(d) / "sessions"
            folder = sessions / f"20261113T152613Z_{UID:016x}"
            make_session(str(folder), 1, 20500)
            with open(folder / "parsed.jsonl", "ab") as f:      # big enough to count as a session
                f.truncate(60 * 1000 * 1000)
            live = Path(d) / "live"
            drive(folder, live)
            self.assertTrue((live / f"{UID:016x}.pkl.gz").is_file())
            self.assertTrue((live / f"{UID:016x}.done").is_file())
            # the report from the live digest has the same key numbers as a full read would
            res_live, _ = session_report.run(folder, Path(d) / "out", Path(d) / "pace.json",
                                             digest_path=live / f"{UID:016x}.pkl.gz")
            self.assertEqual(res_live["digest_cached"], "live")
            self.assertEqual((res_live["your_ms"], res_live["ai_ms"]), (20000, 20500))
            self.assertGreater(len(res_live["points"]), 50)
            self.assertEqual(res_live["setup_differences"]["rearWing"], {"you": 12, "them": 24})
            self.assertIn("Full safety car deployed.", [e["text"] for e in res_live["events"]])

    def test_watcher_uses_marker_within_seconds_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            sessions = Path(d) / "sessions"
            folder = sessions / f"20261113T152613Z_{UID:016x}"
            make_session(str(folder), 1, 20500)
            with open(folder / "parsed.jsonl", "ab") as f:
                f.truncate(60 * 1000 * 1000)
            live = Path(d) / "live"
            drive(folder, live, end_with_send=False)
            calls = []

            def runner(cmd):
                calls.append(cmd)
                dig = cmd[cmd.index("--digest") + 1] if "--digest" in cmd else None
                res, page = session_report.run(cmd[3], Path(d) / "out", Path(d) / "pace.json", digest_path=dig)
                return True, f"Report: {page}\n"
            state = {"since_epoch": time.time() - 60}
            with mock.patch.object(auto_report, "RUN", Path(d) / "run"), \
                 mock.patch.object(auto_report, "LATEST", Path(d) / "run" / "latest.txt"), \
                 mock.patch.object(auto_report, "LOG", Path(d) / "run" / "log.txt"):
                # the file was written moments ago, so the slow path would still be waiting 90 s
                handled = auto_report.tick(state, runner=runner, sessions=sessions, live=live)
            self.assertEqual(handled, [folder.name])
            self.assertIn("--digest", calls[0])
            self.assertIn("(live digest)", state["done"][folder.name])
            self.assertFalse((live / f"{UID:016x}.done").exists())
            self.assertTrue((Path(d) / "out" / f"{folder.name}.html").is_file())


if __name__ == "__main__":
    unittest.main()
