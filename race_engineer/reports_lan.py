"""Reports for the iPad (or any device on the home wifi): a SEPARATE, read-only web page.

  python -m race_engineer.reports_lan run [--port 8780]     (started and stopped by `control on` / `off`)
  python -m race_engineer.reports_lan link

It serves only the after-session reports (reports_web), nothing else: no questions, no controls, no
session data. Every address starts with a long secret code, kept in run/reports_token.txt (created the
first time), and anything without it gets a plain 404. Only GET is answered.
  http://<pc name or ip>:8780/<code>/            list     /<code>/latest     /<code>/<report>.html
Windows Firewall must allow TCP 8780 from the local network (one rule, see `control` notes).
The engineer's own address (127.0.0.1:8777) is unchanged and is still not reachable from the network.
"""
import argparse
import hmac
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import reports_web

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "run" / "reports_token.txt"
PORT = 8780


def get_token(path=TOKEN_FILE):
    try:
        t = Path(path).read_text(encoding="utf-8").strip()
        if len(t) >= 24:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(24)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(t, encoding="utf-8")
    return t


def lan_ip():
    """This PC's address on the local network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def make_server(host, port, token, reports_dir=reports_web.DEFAULT_REPORTS):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parts = self.path.split("?")[0].split("/")          # ['', token, rest...]
            if len(parts) < 2 or not hmac.compare_digest(parts[1].encode(), token.encode()):
                return self._send(404, "")
            base = f"/{parts[1]}"
            rest = "/".join(parts[2:])
            if rest in ("", "index"):
                return self._send(200, reports_web.index_html(reports_dir, base))
            if rest == "latest":
                name = reports_web.latest(reports_dir)
                return (self._send(302, "", extra=[("Location", f"{base}/{name}")]) if name
                        else self._send(404, "<p>No reports yet.</p>"))
            page = reports_web.read_report(reports_dir, rest)
            return self._send(200, page) if page is not None else self._send(404, "")

        def log_message(self, *a):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def link(port=PORT):
    t = get_token()
    return f"http://{lan_ip()}:{port}/{t}/latest"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "link"])
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args(argv)
    if a.cmd == "link":
        print(link(a.port))
        return
    srv = make_server("0.0.0.0", a.port, get_token())
    print(f"reports for the iPad on port {a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
