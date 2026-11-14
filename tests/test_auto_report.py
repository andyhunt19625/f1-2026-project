import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from race_engineer import auto_report


def folder(root, name, mb, age_s):
    f = Path(root) / name
    f.mkdir()
    p = f / "parsed.jsonl"
    with open(p, "wb") as fh:
        fh.truncate(int(mb * 1e6))
    t = time.time() - age_s
    os.utime(p, (t, t))
    return f


class AutoReportTests(unittest.TestCase):
    def test_only_new_finished_big_unreported_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            folder(d, "20261112T100000Z_old", 100, 7200)        # finished before auto-report started
            folder(d, "20261113T160000Z_live", 100, 5)          # still being written
            folder(d, "20261113T160500Z_menu", 1, 600)          # menu stub
            folder(d, "20261113T161000Z_done", 100, 600)        # already reported
            folder(d, "20261113T162000Z_new", 100, 600)         # the one to report
            folder(d, "20261113T152613Z_china", 100, 300)       # started BEFORE the watcher, finished after
            state = {"since_epoch": time.time() - 3600, "done": {"20261113T161000Z_done": "ok"}}
            self.assertEqual([f.name for f in auto_report.candidates(state, d)],
                             ["20261113T152613Z_china", "20261113T162000Z_new"])

    def test_quiet_folder_uses_saved_live_digest_when_there_is_no_marker(self):
        with tempfile.TemporaryDirectory() as d:
            live = Path(d) / "live"
            live.mkdir()
            folder(d, "20261113T162000Z_a8385207b9f1f036b"[:17] + "8385207b9f1f036b", 100, 600)
            (live / "8385207b9f1f036b.pkl.gz").write_bytes(b"x")          # saved after a lap, never marked done
            calls = []

            def fake(cmd):
                calls.append(cmd)
                return True, "Report: D:\\r\\x.html\n"
            state = {"since_epoch": time.time() - 3600}
            with mock.patch.object(auto_report, "RUN", Path(d) / "run"), \
                 mock.patch.object(auto_report, "LATEST", Path(d) / "run" / "l.txt"), \
                 mock.patch.object(auto_report, "LOG", Path(d) / "run" / "log.txt"):
                auto_report.tick(state, runner=fake, sessions=d, live=live)
            self.assertIn("--digest", calls[0])

    def test_provisional_quiet_report_is_replaced_when_the_engineer_marks_the_session_done(self):
        with tempfile.TemporaryDirectory() as d:
            live = Path(d) / "live"
            live.mkdir()
            f = folder(d, "20261113T162000Z_8385207b9f1f036b", 100, 600)
            calls = []

            def fake(cmd):
                calls.append(cmd)
                return True, "Report: D:\\r\\x.html\n"
            state = {"since_epoch": time.time() - 3600}
            with mock.patch.object(auto_report, "RUN", Path(d) / "run"), \
                 mock.patch.object(auto_report, "LATEST", Path(d) / "run" / "l.txt"), \
                 mock.patch.object(auto_report, "LOG", Path(d) / "run" / "log.txt"):
                auto_report.tick(state, runner=fake, sessions=d, live=live)           # quiet pause: provisional report
                self.assertEqual(len(calls), 1)
                self.assertIn(f.name, state["provisional"])
                (live / "8385207b9f1f036b.pkl.gz").write_bytes(b"x")
                (live / "8385207b9f1f036b.done").write_bytes(b"")                      # the session really ends
                auto_report.tick(state, runner=fake, sessions=d, live=live)
                self.assertEqual(len(calls), 2)
                self.assertIn("--digest", calls[1])
                self.assertEqual(state["provisional"], [])
                auto_report.tick(state, runner=fake, sessions=d, live=live)           # nothing more to do
                self.assertEqual(len(calls), 2)

    def test_tick_runs_report_records_result_and_latest(self):
        with tempfile.TemporaryDirectory() as d:
            folder(d, "20261113T162000Z_a", 100, 600)
            folder(d, "20261113T163000Z_b", 100, 600)
            seen = []

            def fake(cmd):
                seen.append(cmd[-1])
                if cmd[-1].endswith("_b"):
                    return False, "Traceback: boom"
                return True, "Race at Melbourne\nReport: D:\\reports\\20261113T162000Z_a.html\n"
            state = {"since_epoch": time.time() - 3600}
            with mock.patch.object(auto_report, "RUN", Path(d) / "run"), \
                 mock.patch.object(auto_report, "LATEST", Path(d) / "run" / "latest.txt"), \
                 mock.patch.object(auto_report, "LOG", Path(d) / "run" / "log.txt"):
                handled = auto_report.tick(state, runner=fake, sessions=d)
                self.assertEqual(handled, ["20261113T162000Z_a", "20261113T163000Z_b"])     # oldest first
                self.assertTrue(state["done"]["20261113T162000Z_a"].startswith("ok"))
                self.assertTrue(state["done"]["20261113T163000Z_b"].startswith("error"))   # recorded, not retried
                self.assertEqual((Path(d) / "run" / "latest.txt").read_text(), "D:\\reports\\20261113T162000Z_a.html")
                self.assertIn("boom", (Path(d) / "run" / "log.txt").read_text())
                self.assertEqual(auto_report.tick(state, runner=fake, sessions=d), [])


if __name__ == "__main__":
    unittest.main()
