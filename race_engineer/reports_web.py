"""Serve the after-session reports through the engineer's own web address, so there is one link to open.

  http://127.0.0.1:8777/reports          list of every report, newest first
  http://127.0.0.1:8777/reports/latest   the newest report
  http://127.0.0.1:8777/reports/<folder>.html

Only files named like a session folder (<UTC time>_<16 hex digits>.html) inside the reports folder
are served, nothing else. The address works on the PC only (the engineer listens on 127.0.0.1).
"""
import html
import json
import re
from pathlib import Path

DEFAULT_REPORTS = Path(__file__).resolve().parent.parent.parent / "reports"
NAME = re.compile(r"^\d{8}T\d{6}Z_[0-9a-f]{16}\.html$")


def _ms(ms):
    if ms is None:
        return "–"
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


def list_reports(folder):
    out = []
    for p in sorted(Path(folder).glob("*.html"), reverse=True) if Path(folder).is_dir() else []:
        if not NAME.match(p.name):
            continue
        row = {"name": p.name, "session": "Session", "track": "", "you": None, "gap": None, "when": p.name[:15]}
        try:
            d = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
            row.update(session=d.get("session", "Session"), track=d.get("track", ""), you=d.get("your_ms"),
                       gap=(d["your_ms"] - d["ai_ms"]) if d.get("your_ms") and d.get("ai_ms") else None,
                       attempt=(d.get("attempts") or {}).get("number"), of=(d.get("attempts") or {}).get("of"))
        except (OSError, ValueError, KeyError):
            pass
        out.append(row)
    return out


def latest(folder):
    rows = list_reports(folder)
    return rows[0]["name"] if rows else None


def index_html(folder):
    rows = list_reports(folder)
    body = "".join(
        f'<tr><td><a href="/reports/{html.escape(r["name"])}">{html.escape(r["session"])}'
        f'{" (attempt %s of %s)" % (r["attempt"], r["of"]) if r.get("of") and r["of"] > 1 else ""}</a></td>'
        f'<td>{html.escape(r["track"])}</td><td>{_ms(r["you"])}</td>'
        f'<td class="{"g" if r["gap"] is not None and r["gap"] <= 0 else "l"}">'
        f'{"–" if r["gap"] is None else ("%+.3f s" % (r["gap"] / 1000))}</td>'
        f'<td class="m">{html.escape(r["when"])}</td></tr>' for r in rows) or '<tr><td colspan="5">No reports yet.</td></tr>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Session Reports</title>
<style>
:root{{--bg:#f3f5f7;--panel:#fff;--fg:#14181d;--muted:#5d6874;--line:#d5dbe1;--gain:#0f9d58;--loss:#d93025;--accent:#1f5fbf}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1216;--panel:#161b21;--fg:#e8edf2;--muted:#94a0ad;--line:#2a323b;--gain:#34c77b;--loss:#ff5f52;--accent:#6aa3ff;color-scheme:dark}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:20px max(16px,env(safe-area-inset-left))}}
main{{max-width:900px;margin:0 auto}} h1{{font-size:1.8rem;margin:0 0 6px}}
p{{color:var(--muted);margin:0 0 14px}} .t{{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}} th{{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.06em}}
a{{color:var(--accent);font-weight:600}} .g{{color:var(--gain)}} .l{{color:var(--loss)}} .m{{color:var(--muted);font-size:.85rem}}
</style></head><body><main><h1>Session reports</h1>
<p>Newest first. The gap is your best lap against the best AI lap (minus = you were quicker). <a href="/reports/latest">Open the latest</a></p>
<div class="t"><table><tr><th>Session</th><th>Track</th><th>Your best</th><th>vs best AI</th><th>Recorded (UTC)</th></tr>{body}</table></div>
</main></body></html>"""


def read_report(folder, name):
    """The report file's bytes, or None if the name is not a report or the file is missing."""
    if not NAME.match(name):
        return None
    p = Path(folder) / name
    try:
        return p.read_bytes()
    except OSError:
        return None
