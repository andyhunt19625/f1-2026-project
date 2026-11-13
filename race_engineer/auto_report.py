"""Build the after-session report automatically when a session ends.

  python -m race_engineer.auto_report run        (started and stopped by `control on` / `control off`)
  python -m race_engineer.auto_report status

Every POLL_S seconds it looks in sessions\\ for folders that
- were recorded after auto-report was first switched on (it never works through old sessions by itself),
- have a parsed.jsonl of at least MIN_MB, and
- have not been written to for QUIET_S seconds (the session is over),
and that have no report yet. For each, oldest first, it runs `session_report` in a separate low-priority
process, so a problem in one session cannot stop the watcher and the game keeps the CPU.
Results are kept in run/auto_report_state.json; the latest report path is in run/latest_report.txt.
Stops cleanly when run/auto_report.stop exists. Read-only on the session data.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
SESSIONS = ROOT.parent / "sessions"
STATE = RUN / "auto_report_state.json"
STOP = RUN / "auto_report.stop"
LATEST = RUN / "latest_report.txt"
LOG = RUN / "auto_report.log"
POLL_S, QUIET_S, MIN_MB = 30, 90, 50
BELOW_NORMAL = 0x00004000          # Windows BELOW_NORMAL_PRIORITY_CLASS


def _now():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_state(path=STATE):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state, path=STATE):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def candidates(state, sessions=SESSIONS, now=None, quiet_s=QUIET_S, min_mb=MIN_MB):
    """Finished, unreported session folders recorded since auto-report started, oldest first."""
    now = time.time() if now is None else now
    since = state.get("since", "")
    done = state.get("done", {})
    out = []
    for f in sorted(Path(sessions).iterdir()) if Path(sessions).is_dir() else []:
        p = f / "parsed.jsonl"
        if not f.is_dir() or f.name[:16] < since or f.name in done or not p.is_file():
            continue
        st = p.stat()
        if st.st_size >= min_mb * 1e6 and now - st.st_mtime >= quiet_s:
            out.append(f)
    return out


def report_one(folder, runner=None):
    """Run session_report for one folder in its own low-priority process. Returns (ok, text)."""
    cmd = [sys.executable, "-m", "race_engineer.session_report", str(folder)]
    if runner:
        return runner(cmd)
    kw = {"creationflags": BELOW_NORMAL} if os.name == "nt" else {}
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, **kw)
    return r.returncode == 0, (r.stdout + r.stderr)[-2000:]


def log(text):
    RUN.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{_now()} {text}\n")


def tick(state, runner=None, sessions=SESSIONS, now=None):
    """One pass: report every finished session. Returns the folders handled."""
    handled = []
    for folder in candidates(state, sessions, now):
        t0 = time.time()
        ok, text = report_one(folder, runner)
        state.setdefault("done", {})[folder.name] = ("ok" if ok else "error") + f" in {time.time() - t0:.0f} s"
        if ok:
            line = next((l for l in text.splitlines() if l.startswith("Report: ")), None)
            if line:
                LATEST.parent.mkdir(parents=True, exist_ok=True)
                LATEST.write_text(line[len("Report: "):].strip(), encoding="utf-8")
        log(f"{folder.name}: {state['done'][folder.name]}\n{text.strip()}")
        handled.append(folder.name)
    return handled


def run():
    state = load_state()
    if not state.get("since"):
        state["since"] = _now()[:16]       # only sessions recorded from now on
        save_state(state)
    log(f"auto-report started; reporting sessions recorded since {state['since']}")
    while not STOP.exists():
        try:
            if tick(state):
                save_state(state)
        except Exception as e:             # keep watching; say what broke
            log(f"error: {e!r}")
        for _ in range(POLL_S):
            if STOP.exists():
                break
            time.sleep(1)
    STOP.unlink(missing_ok=True)
    log("auto-report stopped")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "status"])
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run()
    else:
        s = load_state()
        print(f"Reporting sessions recorded since {s.get('since', '(not started yet)')}.")
        for k, v in sorted(s.get("done", {}).items())[-10:]:
            print(f"  {k}: {v}")
        if LATEST.is_file():
            print(f"Latest report: {LATEST.read_text(encoding='utf-8')}")


if __name__ == "__main__":
    main()
