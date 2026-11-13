"""Turn the whole engineer on or off with one command.

  python3 -m race_engineer.control on [--pit-loss S] [--buffer S]
  python3 -m race_engineer.control off [--force]
  python3 -m race_engineer.control status

on  : capture first (if not already running), then the engineer program, then the after-session
      report watcher (auto_report.py).
off : the report watcher, the engineer, then capture.
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
ROOT = Path(__file__).resolve().parent.parent          # .../esports/engineer (or D:\RealF1-esports\engineer)
RUN_DIR = ROOT / "run"
PIDFILE = RUN_DIR / "engineer.pid"
LOGFILE = RUN_DIR / "engineer.log"
SESSIONS_DIR = ROOT.parent / "sessions"                # .../sessions next to the engineer folder
ACTIVE_WITHIN_S = 20                                   # a session written this recently is live
IS_WINDOWS = os.name == "nt"
# Windows (the gaming PC): capture runs as an ordinary background process, no service.
CAPTURE_DIR = ROOT.parent / "capture"
CAPTURE_PID = RUN_DIR / "capture.pid"
CAPTURE_OUT = RUN_DIR / "capture.out"
AUTO_PID = RUN_DIR / "auto_report.pid"
AUTO_OUT = RUN_DIR / "auto_report.out"
AUTO_STOP = RUN_DIR / "auto_report.stop"
LAN_PID = RUN_DIR / "reports_lan.pid"
LAN_OUT = RUN_DIR / "reports_lan.out"
STOP_FILE = CAPTURE_DIR / "STOP"                       # udp_capture.py (PC copy) exits cleanly when this exists
WIN_DETACHED = 0x00000008 | 0x00000200                  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
WIN_BREAKAWAY = 0x01000000                              # CREATE_BREAKAWAY_FROM_JOB


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def _win_cmdline(pid, runner=_run):
    """Command line of a Windows process, or '' if it is not running."""
    r = runner(["powershell", "-NoProfile", "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"])
    return (r.stdout or "").strip()


def _pid_running(pidfile, marker, runner=_run):
    """PID from pidfile if that process is alive and its command line contains marker."""
    try:
        pid = int(Path(pidfile).read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        if IS_WINDOWS:
            cmd = _win_cmdline(pid, runner)
        else:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        cmd = ""
    if marker in cmd:
        return pid
    try:
        Path(pidfile).unlink()
    except OSError:
        pass
    return None


def _spawn(args, cwd, out_path):
    """Start a background process that outlives the command that started it."""
    RUN_DIR.mkdir(exist_ok=True)
    out = open(out_path, "ab")
    if not IS_WINDOWS:
        return subprocess.Popen(args, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    try:
        return subprocess.Popen(args, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=WIN_DETACHED | WIN_BREAKAWAY)
    except OSError:   # breakaway not allowed by the parent job: start without it
        return subprocess.Popen(args, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=WIN_DETACHED)


def capture_state(runner=_run):
    if IS_WINDOWS:
        return "active" if _pid_running(CAPTURE_PID, "udp_capture.py", runner) else "inactive"
    return runner(["systemctl", "is-active", SERVICE]).stdout.strip()


def start_capture(runner=_run):
    if IS_WINDOWS:
        if not (CAPTURE_DIR / "udp_capture.py").is_file():
            return False, f"{CAPTURE_DIR / 'udp_capture.py'} not found"
        try:
            STOP_FILE.unlink()
        except OSError:
            pass
        proc = _spawn([sys.executable, str(CAPTURE_DIR / "udp_capture.py")], CAPTURE_DIR, CAPTURE_OUT)
        CAPTURE_PID.write_text(str(proc.pid))
        return True, ""
    r = runner(["sudo", "-n", "systemctl", "start", SERVICE])
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def stop_capture(runner=_run):
    if IS_WINDOWS:
        # Ask it to stop cleanly (closes the session and flushes files). Never killed here.
        STOP_FILE.write_text("stop\n")
        if wait_for(lambda: _pid_running(CAPTURE_PID, "udp_capture.py", runner) is None, 10):
            try:
                STOP_FILE.unlink()
            except OSError:
                pass
            return True, ""
        return False, "capture did not exit within 10 s after the stop file was written"
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


def engineer_pid(runner=_run):
    """PID of the running engineer program, or None. Clears a stale pid file."""
    return _pid_running(PIDFILE, "race_engineer.server", runner)


def wait_for(cond, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.25)
    return cond()


def start_engineer(extra_args):
    proc = _spawn([sys.executable, "-m", "race_engineer.server", *extra_args], ROOT, LOGFILE)
    PIDFILE.write_text(str(proc.pid))
    return proc.pid


def auto_pid(runner=_run):
    return _pid_running(AUTO_PID, "race_engineer.auto_report", runner)


def start_auto():
    """The after-session report watcher (auto_report.py). Never blocks capture or the engineer."""
    if auto_pid():
        return f"Auto report: already running (pid {auto_pid()})."
    AUTO_STOP.unlink(missing_ok=True)
    proc = _spawn([sys.executable, "-m", "race_engineer.auto_report", "run"], ROOT, AUTO_OUT)
    AUTO_PID.write_text(str(proc.pid))
    return f"Auto report: started (pid {proc.pid})."


def stop_auto():
    pid = auto_pid()
    if not pid:
        return "Auto report: was not running."
    AUTO_STOP.write_text("stop")
    if wait_for(lambda: auto_pid() is None, 40):
        return "Auto report: stopped."
    return f"Auto report (pid {pid}) is finishing a report and will stop after it."


def lan_pid(runner=_run):
    return _pid_running(LAN_PID, "race_engineer.reports_lan", runner)


def start_lan():
    """Read-only reports page for the iPad on the home wifi (reports_lan.py)."""
    if lan_pid():
        return f"iPad reports: already running (pid {lan_pid()})."
    proc = _spawn([sys.executable, "-m", "race_engineer.reports_lan", "run"], ROOT, LAN_OUT)
    LAN_PID.write_text(str(proc.pid))
    return f"iPad reports: started (pid {proc.pid})."


def stop_lan():
    pid = lan_pid()
    if not pid:
        return "iPad reports: was not running."
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    return "iPad reports: stopped." if wait_for(lambda: lan_pid() is None, 6) else "iPad reports: did not stop in time."


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
    print(start_auto())
    print(start_lan())
    return cmd_status(args)


ENGINEER_STOP = RUN_DIR / "engineer.stop"


def stop_engineer():
    """Ask the engineer to stop cleanly (it saves the session digest and marks it finished, so the
    last report is built); only if it does not, stop it by force."""
    pid = engineer_pid()
    if not pid:
        return True, "Engineer: was not running."
    RUN_DIR.mkdir(exist_ok=True)
    ENGINEER_STOP.write_text("stop")
    if wait_for(lambda: engineer_pid() is None, 10):
        return True, "Engineer: stopped."
    ENGINEER_STOP.unlink(missing_ok=True)
    os.kill(pid, signal.SIGTERM)
    if wait_for(lambda: engineer_pid() is None, 6):
        return True, "Engineer: stopped (by force)."
    return False, f"Engineer (pid {pid}) did not stop in time."


def wait_reports_built(seconds=40):
    """Give the report watcher time to build the report for any session just marked finished."""
    from .auto_report import LIVE
    wait_for(lambda: not list(Path(LIVE).glob("*.done")) if Path(LIVE).is_dir() else True, seconds)


def cmd_restart_engineer(args):
    """Reload the engineer program (e.g. after a code update) WITHOUT touching capture."""
    ok, msg = stop_engineer()
    print(msg)
    if not ok:
        return 1
    if capture_state() != "active":
        print("Capture is not running, so the engineer was not restarted. Use 'on'.")
        return 1
    pid = start_engineer([])
    if not wait_for(lambda: fetch("/status")[0] == 200, 8):
        print(f"Engineer did not come up (pid {pid}). Log:\n{log_tail()}")
        return 1
    print(f"Engineer: restarted (pid {pid}). Capture was not touched.")
    return cmd_status(args)


def cmd_off(args):
    if session_in_progress() and not args.force:
        print("A session was written to in the last "
              f"{ACTIVE_WITHIN_S} seconds, so a race may be in progress. Not stopping. "
              "Run 'off --force' only if you are sure.")
        return 1
    ok, msg = stop_engineer()           # first: it marks the finished session
    print(msg)
    if not ok:
        print("Capture left running.")
        return 1
    wait_reports_built()                # the report watcher builds that session's report (seconds)
    print(stop_auto())
    print(stop_lan())
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


def listening(ports=(20777, 20778), runner=_run, windows=None):
    """Which of the given UDP ports have a listener: `ss -ulnH` on Linux, `netstat -ano -p UDP`
    on Windows (no admin needed for either)."""
    windows = IS_WINDOWS if windows is None else windows
    try:
        out = runner(["netstat", "-ano", "-p", "UDP"] if windows else ["ss", "-ulnH"]).stdout
    except OSError:
        return {p: None for p in ports}
    col = 1 if windows else 3      # local address column
    return {p: any(line.split()[col].endswith(f":{p}") for line in out.splitlines()
                   if len(line.split()) > col and (not windows or line.split()[0] == "UDP"))
            for p in ports}


def cmd_status(args):
    print(f"Capture: {capture_state()}")
    pid = engineer_pid()
    print(f"Engineer: {'running (pid %d)' % pid if pid else 'not running'}")
    ap_ = auto_pid()
    print(f"Auto report: {'running (pid %d)' % ap_ if ap_ else 'not running'}")
    lp = lan_pid()
    print(f"iPad reports: {'running (pid %d)' % lp if lp else 'not running'}")
    ports = listening()
    word = {True: "yes", False: "no", None: "unknown (port check not available)"}
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
    sub.add_parser("restart-engineer")
    a = ap.parse_args(argv)
    return {"on": cmd_on, "off": cmd_off, "status": cmd_status,
            "restart-engineer": cmd_restart_engineer}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
