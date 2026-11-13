"""Simulator: replay a real recorded session (raw.bin) as live UDP into a SEPARATE test engineer
and question it hard, to find what holds answers back before it matters in a race.

  python -m race_engineer.sim PATH/raw.bin [--speeds 1,2,4,8] [--threads 4] [--every 0.5]
                                           [--minutes N]   (recorded minutes per run; default all)

For each speed:
  1. start a test engineer: questions on 127.0.0.1:8778, feed on 127.0.0.1:20779, lap log in run/sim/
     (the real engineer on 8777/20778 and the capture are never touched);
  2. send the recording's datagrams to 20779 at that speed (2 = twice as fast as the game);
  3. meanwhile run the drill every --every seconds plus --threads threads asking random
     questions back to back;
  4. compare datagrams sent with datagrams the engineer received (dropped), decode errors,
     lap reports made, answer times and errors; stop the test engineer.
Writes run/sim/sim_<time>.txt (summary) and one drill log per speed. Read-only on the recording.
"""
import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import ask, drill
from .send_raw import frames

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "run" / "sim"
Q_PORT, FEED_PORT = 8778, 20779


def _mem_mb(pid):
    try:
        import psutil
        return round(psutil.Process(pid).memory_info().rss / 1e6)
    except Exception:
        return None


def start_engineer(lap_log, parser=None):
    cmd = [sys.executable, "-m", "race_engineer.server", "--port", str(Q_PORT),
           "--feed-port", str(FEED_PORT), "--lap-log", str(lap_log),
           "--circuit-store", str(OUT / "circuit_data_sim.json")]   # never the real store
    if parser:
        cmd += ["--parser", str(parser)]
    p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(50):
        code, _ = ask.fetch("/status", "127.0.0.1", Q_PORT, timeout=1)
        if code == 200:
            return p
        if p.poll() is not None:
            raise SystemExit("Test engineer failed to start: " + p.stderr.read().decode(errors="replace")[-500:])
        time.sleep(0.2)
    p.kill()
    raise SystemExit("Test engineer did not answer on port 8778.")


def send(path, speed, minutes, counter):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    t0 = wall0 = None
    late = 0.0
    for t, data in frames(path):
        if t0 is None:
            t0, wall0 = t, time.monotonic()
        if minutes is not None and t - t0 > minutes * 60:
            break
        delay = (t - t0) / speed - (time.monotonic() - wall0)
        if delay > 0:
            time.sleep(delay)
        else:
            late = max(late, -delay)
        sock.sendto(data, ("127.0.0.1", FEED_PORT))
        counter[0] += 1
    sock.close()
    return late


def one_run(path, speed, threads, every, minutes, stamp, parser=None):
    lap_log = OUT / f"laps_{stamp}_x{speed:g}.txt"
    log = OUT / f"drill_{stamp}_x{speed:g}.jsonl"
    eng = start_engineer(lap_log, parser)
    stop = threading.Event()
    d = drill.Drill(log, port=Q_PORT)
    d._log({"kind": "start", "speed": speed, "threads": threads, "every_s": every})

    def drill_loop():
        while not stop.is_set():
            t0 = time.time()
            try:
                d.step()
            except Exception as e:
                d._log({"kind": "event", "event": "drill_error", "detail": repr(e)[:300]})
            stop.wait(max(0.0, every - (time.time() - t0)))

    workers = [threading.Thread(target=drill_loop, daemon=True)]
    workers += [threading.Thread(target=d.hammer, args=(stop,), daemon=True) for _ in range(threads)]
    mem = []
    counter = [0]

    def mem_loop():
        while not stop.is_set():
            m = _mem_mb(eng.pid)
            if m is not None:
                mem.append(m)
            stop.wait(5)

    workers.append(threading.Thread(target=mem_loop, daemon=True))
    for w in workers:
        w.start()
    t_start = time.time()
    late = send(path, speed, minutes, counter)
    wall = time.time() - t_start
    time.sleep(3)                     # let the engineer finish what it has
    code, st = ask.fetch("/status", "127.0.0.1", Q_PORT, timeout=5)
    stop.set()
    for w in workers:
        w.join(timeout=10)
    eng.terminate()
    try:
        eng.wait(timeout=10)
    except subprocess.TimeoutExpired:
        eng.kill()
    d._log({"kind": "stop", "reason": "sim done"})
    got = st.get("datagrams_received") if code == 200 else None
    lines = [f"--- speed x{speed:g}: {counter[0]} datagrams sent in {wall:.0f} s "
             f"(sender fell behind by up to {late:.2f} s) ---"]
    if code == 200:
        lost = counter[0] - (got or 0)
        lines.append(f"Engineer received {got} ({lost} lost, {100 * lost / max(1, counter[0]):.2f}%), "
                     f"decode errors {st.get('decode_errors')}, lap reports {st.get('lap_reports')}, "
                     f"lap-analysis errors {st.get('packets_in_state', {}).get('live_laps_errors', 0)}")
    else:
        lines.append(f"Engineer did not answer at the end: {st.get('error')}")
    if mem:
        lines.append(f"Engineer memory: start {mem[0]} MB, peak {max(mem)} MB, end {mem[-1]} MB")
    else:
        lines.append("Engineer memory: not measured (psutil not installed)")
    lines.append(drill.summary(log))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("--speeds", default="1,2,4,8")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--every", type=float, default=0.5)
    ap.add_argument("--minutes", type=float, default=None)
    ap.add_argument("--parser", default=None, help="test only: a different decoder (default: capture/parser.py)")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    report = OUT / f"sim_{stamp}.txt"
    head = (f"Simulator run {stamp}\nRecording: {a.raw} ({os.path.getsize(a.raw) / 1e6:.0f} MB)\n"
            f"Drill every {a.every} s plus {a.threads} threads asking back to back.\n")
    with open(report, "w", encoding="utf-8") as f:
        f.write(head + "\n")
    print(head, flush=True)
    for sp in [float(x) for x in a.speeds.split(",")]:
        text = one_run(a.raw, sp, a.threads, a.every, a.minutes, stamp, a.parser)
        with open(report, "a", encoding="utf-8") as f:
            f.write(text + "\n\n")
        print(text + "\n", flush=True)
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
