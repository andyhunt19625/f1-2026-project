"""Turn the whole engineer on or off with one command.

  python3 -m race_engineer.control on [--pit-loss S] [--buffer S]
  python3 -m race_engineer.control off [--force]
  python3 -m race_engineer.control status

on  : capture first (if not already running), then the engineer program.
off : the engineer first, then capture.
Only touches f1-esports-capture.service (the sudoers rule allows exactly start, stop
and status of that one service) and the engineer's own process. Never touches the
real F1 site or its services.
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .ask import fetch

SERVICE = "f1-esports-capture.service"
ROOT = Path(__file__).resolve().parent.parent          # ~/esports/engineer
RUN_DIR = ROOT / "run"
PIDFILE = RUN_DIR / "engineer.pid"
LOGFILE = RUN_DIR / "engineer.log"
SESSIONS_DIR = ROOT.parent / "sessions"                # ~/esports/sessions
ACTIVE_WITHIN_S = 20                                   # a session written this recently is live


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def capture_state(runner=_run):
    return runner(["systemctl", "is-active", SERVICE]).stdout.strip()


def start_capture(runner=_run):
    r = runner(["sudo", "-n", "systemctl", "start", SERVICE])
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def stop_capture(runner=_run):
    r = runner(["sudo", "-n", "systemctl", "stop", SERVICE])
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def session_in_progress(sessions_dir=SESSIONS_DIR, now=None):
    """True if any session file was written in the last ACTIVE_WITHIN_S seconds."""
    now = now or time.time()
    try:
        folders = [p for p in Path(sessions_dir).iterdir() if p.is_dir()]
    except OSError:
        return False
    for f in sorted(folders, key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
        for name in ("parsed.jsonl", "raw.bin"):
            try:
                if now - (f / name).stat().st_mtime < ACTIVE_WITHIN_S:
                    return True
            except OSError:
                pass
    return False


def engineer_pid():
    """PID of the running engineer program, or None. Clears a stale pid file."""
    try:
        pid = int(PIDFILE.read_text().strip())
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
        if "race_engineer.server" in cmd:
            return pid
    except (OSError, ValueError):
        pass
    try:
        PIDFILE.unlink()
    except OSError:
        pass
    return None


def wait_for(cond, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.25)
    return cond()


def start_engineer(extra_args):
    RUN_DIR.mkdir(exist_ok=True)
    log = open(LOGFILE, "ab")
    proc = subprocess.Popen([sys.executable, "-m", "race_engineer.server", *extra_args],
                            cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    PIDFILE.write_text(str(proc.pid))
    return proc.pid


def log_tail(n=8):
    try:
        return "\n".join(LOGFILE.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "(no log)"


def cmd_on(args):
    if capture_state() != "active":
        ok, msg = start_capture()
        if not ok or not wait_for(lambda: capture_state() == "active", 6):
            print(f"Could not start capture: {msg or 'it did not become active'}. Engineer NOT started.")
            return 1
        print("Capture: started.")
    else:
        print("Capture: already running.")
    if engineer_pid():
        print(f"Engineer: already running (pid {engineer_pid()}).")
    else:
        extra = []
        if args.pit_loss is not None:
            extra += ["--pit-loss", str(args.pit_loss)]
        if args.buffer is not None:
            extra += ["--buffer", str(args.buffer)]
        pid = start_engineer(extra)
        if not wait_for(lambda: fetch("/status")[0] == 200, 8):
            tail = log_tail()
            if "Address already in use" in tail:
                print("Engineer did not start: its port is already taken. Is a "
                      "'race_engineer.cli --live' console still open? Type 'quit' in it, then turn on again.")
            print(f"Engineer did not come up (pid {pid}). Log:\n{tail}")
            return 1
        print(f"Engineer: started (pid {pid}).")
    return cmd_status(args)


def cmd_off(args):
    if session_in_progress() and not args.force:
        print("A session was written to in the last "
              f"{ACTIVE_WITHIN_S} seconds, so a race may be in progress. Not stopping. "
              "Run 'off --force' only if you are sure.")
        return 1
    pid = engineer_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
        if wait_for(lambda: engineer_pid() is None, 6):
            print("Engineer: stopped.")
        else:
            print(f"Engineer (pid {pid}) did not stop in time. Capture left running.")
            return 1
    else:
        print("Engineer: was not running.")
    if capture_state() == "active":
        ok, msg = stop_capture()
        if not ok or not wait_for(lambda: capture_state() != "active", 8):
            print(f"Could not stop capture: {msg or 'it is still active'}")
            return 1
        print("Capture: stopped.")
    else:
        print("Capture: was not running.")
    print("Reminder: any session folders from this run need your save-or-delete decision.")
    return 0


def listening(ports=(20777, 20778), runner=_run):
    """Which of the given UDP ports have a listener (from `ss -ulnH`, no sudo)."""
    try:
        out = runner(["ss", "-ulnH"]).stdout
    except OSError:
        return {p: None for p in ports}
    return {p: any(line.split()[3].endswith(f":{p}") for line in out.splitlines()
                   if len(line.split()) > 3) for p in ports}


def cmd_status(args):
    print(f"Capture: {capture_state()}")
    pid = engineer_pid()
    print(f"Engineer: {'running (pid %d)' % pid if pid else 'not running'}")
    ports = listening()
    word = {True: "yes", False: "no", None: "unknown (ss not available)"}
    print(f"Listening: capture port 20777 {word[ports[20777]]}, engineer port 20778 {word[ports[20778]]}")
    if pid:
        code, data = fetch("/status")
        print(f"  status: {data}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    on = sub.add_parser("on")
    on.add_argument("--pit-loss", type=float, default=None)
    on.add_argument("--buffer", type=float, default=None)
    off = sub.add_parser("off")
    off.add_argument("--force", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    return {"on": cmd_on, "off": cmd_off, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
