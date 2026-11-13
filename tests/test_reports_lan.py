import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from race_engineer import reports_lan

NAME = "20261113T195659Z_8a0d6b674200c5c5"


def get(port, path, method="GET"):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"x" if method == "POST" else None)
    try:
        r = urllib.request.build_opener(NoRedirect).open(req, timeout=5)
        return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


class LanTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        (d / f"{NAME}.html").write_text("<p>the report</p>")
        (d / f"{NAME}.json").write_text(json.dumps({"session": "Race", "track": "Shanghai", "your_ms": 1, "ai_ms": 2}))
        (d / "notes.html").write_text("secret")
        self.token = reports_lan.get_token(d / "token.txt")
        self.srv = reports_lan.make_server("127.0.0.1", 0, self.token, d)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.dir.cleanup()

    def test_token_required_for_everything(self):
        for path in ("/", "/reports", "/latest", f"/{NAME}.html", "/wrong/", "/wrong/latest", f"/{self.token}x/"):
            self.assertEqual(get(self.port, path)[0], 404, path)

    def test_with_token_index_latest_and_report_links_keep_the_token(self):
        code, body, _ = get(self.port, f"/{self.token}/")
        self.assertEqual(code, 200)
        self.assertIn(f'href="/{self.token}/{NAME}.html"', body)
        code, _, hdr = get(self.port, f"/{self.token}/latest")
        self.assertEqual((code, hdr["Location"]), (302, f"/{self.token}/{NAME}.html"))
        self.assertEqual(get(self.port, f"/{self.token}/{NAME}.html")[:2], (200, "<p>the report</p>"))

    def test_nothing_else_is_served_and_only_get(self):
        for bad in ("notes.html", f"{NAME}.json", "..%2Fx.html", "../token.txt"):
            self.assertEqual(get(self.port, f"/{self.token}/{bad}")[0], 404, bad)
        self.assertNotEqual(get(self.port, f"/{self.token}/", "POST")[0], 200)

    def test_token_is_kept_between_runs(self):
        p = Path(self.dir.name) / "token.txt"
        self.assertEqual(reports_lan.get_token(p), self.token)
        self.assertGreaterEqual(len(self.token), 24)


if __name__ == "__main__":
    unittest.main()
