import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from race_engineer import reports_web
from race_engineer.server import make_server
from race_engineer.state import LiveState


def get(port, path):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        r = urllib.request.build_opener(NoRedirect).open(f"http://127.0.0.1:{port}{path}", timeout=5)
        return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


class ReportsWebTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        for name, you, ai, att in (("20261113T193731Z_1d5947574d2b92fa", 92966, 91986, (1, 4)),
                                   ("20261113T195659Z_8a0d6b674200c5c5", 92235, 92001, (4, 4))):
            (d / f"{name}.html").write_text(f"<p>report {name}</p>")
            (d / f"{name}.json").write_text(json.dumps({"session": "Short sprint shootout", "track": "Shanghai",
                                                        "your_ms": you, "ai_ms": ai,
                                                        "attempts": {"number": att[0], "of": att[1]}}))
        (d / "notes.html").write_text("secret")
        self.httpd = make_server(LiveState(), None, "127.0.0.1", 0, {"reports_dir": d})
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dir.cleanup()

    def test_index_latest_and_report(self):
        code, body, _ = get(self.port, "/reports")
        self.assertEqual(code, 200)
        self.assertIn("attempt 4 of 4", body)
        self.assertLess(body.index("195659Z"), body.index("193731Z"))          # newest first
        self.assertIn("+0.234 s", body)
        code, _, hdr = get(self.port, "/reports/latest")
        self.assertEqual((code, hdr["Location"]), (302, "/reports/20261113T195659Z_8a0d6b674200c5c5.html"))
        code, body, _ = get(self.port, "/reports/20261113T193731Z_1d5947574d2b92fa.html")
        self.assertEqual((code, "report 20261113T193731Z" in body), (200, True))

    def test_only_report_names_are_served(self):
        for bad in ("/reports/notes.html", "/reports/..%2Fsecret.html", "/reports/20261113T193731Z_1d5947574d2b92fa.json",
                    "/reports/..\\x.html"):
            self.assertEqual(get(self.port, bad)[0], 404, bad)

    def test_no_reports_yet(self):
        with tempfile.TemporaryDirectory() as e:
            self.assertIn("No reports yet", reports_web.index_html(e))
            self.assertIsNone(reports_web.latest(e))


if __name__ == "__main__":
    unittest.main()
