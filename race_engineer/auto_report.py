"""Build the after-session report automatically when a session ends.

  python -m race_engineer.auto_report run        (started and stopped by `control on` / `control off`)
  python -m race_engineer.auto_report status

Every 5 seconds it checks for sessions the engineer has marked done (run/live_digests/<uid>.done); it builds
those reports from the engineer's saved digest, which takes seconds because nothing is re-read. Every 30 seconds
it also looks in sessions\\ for folders (the fallback when the engineer was not running) that
- were still being written after auto-report was first switched on (it never works through old sessions by itself),
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
POLL_S, QUIET_S, MIN_MB = 5, 90, 50
LIVE = RUN / "live_digests"          # the engineer writes <uid>.pkl.gz and <uid>.done here (live_laps.py)
MARKER_GIVE_UP_S = 600              # a marker whose session folder never appears is dropped after this
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
    since = state.get("since_epoch")
    if since is None and state.get("since"):        # older state files: folder-name time only
        since = datetime.strptime(state["since"][:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    done = state.get("done", {})
    out = []
    for f in sorted(Path(sessions).iterdir()) if Path(sessions).is_dir() else []:
        p = f / "parsed.jsonl"
        if not f.is_dir() or f.name in done or not p.is_file():
            continue
        st = p.stat()
        # A session counts if it was still being written after auto-report started. (It used to be
        # "started after", which skipped China Practice 1 on 13 Nov 2026: recording began at 15:26,
        # the watcher at 16:02, the session ended at 16:17.)
        if since is not None and st.st_mtime < since:
            continue
        if st.st_size >= min_mb * 1e6 and now - st.st_mtime >= quiet_s:
            out.append(f)
    return out


def live_markers(sessions=SESSIONS, live=LIVE, min_mb=MIN_MB, now=None):
    """(folder, digest path, marker) for each session the engineer has marked done.
    The folder is the biggest one whose name ends with _<uid> (capture names folders <UTC time>_<uid>)."""
    now = time.time() if now is None else now
    out, stale = [], []
    for m in sorted(Path(live).glob("*.done")) if Path(live).is_dir() else []:
        uid = m.stem
        dig = m.with_name(f"{uid}.pkl.gz")
        folders = [f for f in Path(sessions).iterdir() if f.is_dir() and f.name.endswith("_" + uid)
                   and (f / "parsed.jsonl").is_file()] if Path(sessions).is_dir() else []
        folders = [f for f in folders if (f / "parsed.jsonl").stat().st_size >= min_mb * 1e6]
        if folders and dig.is_file():
            out.append((max(folders, key=lambda f: (f / "parsed.jsonl").stat().st_size), dig, m))
        elif now - m.stat().st_mtime > MARKER_GIVE_UP_S:
            stale.append(m)
    return out, stale


def report_one(folder, runner=None, digest=None):
    """Run session_report for one folder in its own low-priority process. Returns (ok, text)."""
    cmd = [sys.executable, "-m", "race_engineer.session_report", str(folder)]
    if digest:
        cmd += ["--digest", str(digest)]
    if runner:
        return runner(cmd)
    kw = {"creationflags": BELOW_NORMAL} if os.name == "nt" else {}
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, **kw)
    return r.returncode == 0, (r.stdout + r.stderr)[-2000:]


def log(text):
    RUN.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{_now()} {text}\n")


def tick(state, runner=None, sessions=SESSIONS, now=None, live=LIVE, include_quiet=True):
    """One pass: report every finished session (engineer's live digests first, then quiet folders).
    Returns the folders handled."""
    handled = []
    jobs = []
    markers, stale = live_markers(sessions, live, now=now)
    for m in stale:
        log(f"no session folder appeared for live digest {m.stem}; using the file-read path instead")
        m.unlink(missing_ok=True)
    for folder, dig, m in markers:
        if folder.name in state.get("done", {}):
            m.unlink(missing_ok=True)
            continue
        jobs.append((folder, dig, m))
    if include_quiet:
        live_names = {f.name for f, _, _ in jobs}
        for f in candidates(state, sessions, now):
            if f.name in live_names:
                continue
            # Quit the game without the "session finished" signal? The engineer saved a digest after
            # every lap, so use it instead of reading the whole file.
            dig = Path(live) / f"{f.name.rsplit('_', 1)[-1]}.pkl.gz"
            jobs.append((f, dig if dig.is_file() else None, None))
    for folder, dig, marker in jobs:
        t0 = time.time()
        ok, text = report_one(folder, runner, dig)
        if dig and not ok:                 # live digest failed: fall back to reading the file
            log(f"{folder.name}: live digest failed, reading the file instead\n{text.strip()}")
            ok, text = report_one(folder, runner)
        if marker:
            marker.unlink(missing_ok=True)
        state.setdefault("done", {})[folder.name] = (("ok" if ok else "error") + f" in {time.time() - t0:.0f} s"
                                                     + (" (live digest)" if dig and ok else ""))
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
    if not state.get("since") and not state.get("since_epoch"):
        state["since"] = _now()[:16]       # only sessions still being written from now on
        state["since_epoch"] = time.time()
        save_state(state)
    log(f"auto-report started; reporting sessions recorded since {state['since']}")
    n = 0
    while not STOP.exists():
        try:
            # live digests every 5 s; the slower file-read fallback every 30 s
            if tick(state, include_quiet=(n % 6 == 0)):
                save_state(state)
        except Exception as e:             # keep watching; say what broke
            log(f"error: {e!r}")
        n += 1
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
        print(f"Reporting sessions still being written after {s.get('since', '(not started yet)')}.")
        for k, v in sorted(s.get("done", {}).items())[-10:]:
            print(f"  {k}: {v}")
        if LATEST.is_file():
            print(f"Latest report: {LATEST.read_text(encoding='utf-8')}")


if __name__ == "__main__":
    main()
