"""Race drill: question the live engineer over and over while a real session runs, the way a
driver would, and log what holds the answers back.

  python -m race_engineer.drill run [--every 3] [--minutes 150]     (stops early if run/drill.stop exists)
  python -m race_engineer.drill summary [run/drill_YYYYmmddTHHMMSSZ.jsonl]   (default: newest)

Every --every seconds it asks /status, then the question tools in turn (gap to a driver, tyres,
damage, pit window, weather, everyone's tyres, last lap; AI deploy and reminders less often), and records for each:
time taken, HTTP code, error text, and a short answer. It also watches for:
- stuck data: packets still arriving but the session clock not moving;
- idle: no packets arriving (menus, pause, replays);
- lap reports: when your lap number goes up, how long until a new lap report exists, or if none comes;
- drying: the forecast showing drier weather within DRYING_SOON_MIN minutes;
- tyres: every change in how many other cars are on dry tyres (often the first sign the track is ready).
Only reads from the engineer (HTTP GET on 127.0.0.1). Writes one JSONL log in run/.
"""
import argparse
import json
import random
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from . import ask

RUN = Path(__file__).resolve().parent.parent / "run"
STOP = RUN / "drill.stop"
LAP_REPORT_WAIT_S = 30
DRYING_SOON_MIN = 10      # minutes ahead that counts as "soon" for a drying forecast (my choice)


def _get(path, host, port):
    t0 = time.perf_counter()
    code, data = ask.fetch(path, host, port, timeout=5)
    return code, data, round((time.perf_counter() - t0) * 1000, 1)


class Drill:
    def __init__(self, log_path, host=ask.DEFAULT_HOST, port=ask.DEFAULT_PORT, clock=time.time):
        self.log_path = Path(log_path)
        self.host, self.port, self.clock = host, port, clock
        self.prev = None
        self.turn = 0
        self.pending_lap = None   # (lap number, wall time seen, report count then)
        self.lock = threading.Lock()
        self.last_weather_alert = None
        self.last_dry_count = None

    def _log(self, rec):
        rec["wall"] = round(self.clock(), 3)
        with self.lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    def _ask(self, tool, **q):
        path = "/ask?" + urlencode(dict(tool=tool, **q))
        code, data, ms = _get(path, self.host, self.port)
        self._log({"kind": "ask", "tool": tool, "q": q, "code": code, "ms": ms,
                   "error": data.get("error"), "answer": (data.get("answer") or "")[:240]})
        return code, data

    def step(self):
        """One round: status, freshness checks, then a set of questions."""
        code, st, ms = _get("/status", self.host, self.port)
        self._log({"kind": "ask", "tool": "status", "code": code, "ms": ms, "error": st.get("error")})
        if code != 200:
            self._log({"kind": "event", "event": "engineer_unreachable", "detail": st.get("error")})
            self.prev = None
            return
        now = self.clock()
        if self.prev:
            d_pkts = (st.get("datagrams_received") or 0) - (self.prev.get("datagrams_received") or 0)
            d_err = (st.get("decode_errors") or 0) - (self.prev.get("decode_errors") or 0)
            if d_pkts <= 0:
                self._log({"kind": "event", "event": "idle_no_packets"})
            elif st.get("session_time") == self.prev.get("session_time"):
                self._log({"kind": "event", "event": "stuck_clock", "packets": d_pkts})
            if d_err > 0:
                self._log({"kind": "event", "event": "decode_errors", "count": d_err})
            lap, prev_lap = st.get("your_lap"), self.prev.get("your_lap")
            if lap is not None and prev_lap is not None and lap > prev_lap and self.pending_lap is None:
                self.pending_lap = (prev_lap, now, self.prev.get("lap_reports") or 0)
        if self.pending_lap:
            done_lap, seen, count0 = self.pending_lap
            if (st.get("lap_reports") or 0) > count0:
                self._log({"kind": "event", "event": "lap_report", "lap": done_lap,
                           "delay_s": round(now - seen, 1)})
                self.pending_lap = None
            elif now - seen > LAP_REPORT_WAIT_S:
                self._log({"kind": "event", "event": "lap_report_missing", "lap": done_lap})
                self.pending_lap = None
        self.prev = st
        if not st.get("ready"):
            return
        others = [n for n in (st.get("drivers") or []) if n != st.get("you")]
        if others:
            self._ask("gap_to", driver=others[self.turn % len(others)])
        self._ask("tyre_status")
        self._ask("damage_status")
        self._ask("pit_window")
        if self.turn % 10 == 0:
            self._ask("pit_loss")
        code, w = self._ask("weather_change")
        if code == 200:
            self._weather_watch(w)
        code, tf = self._ask("tyre_field")
        if code == 200:
            self._tyre_watch(tf)
        self._ask("last_lap")
        if self.turn % 10 == 0:
            self._ask("ai_deploy")
            self._ask("reminders")
        self.turn += 1


    def hammer(self, stop, drivers=()):
        """Ask random questions back to back until stop is set (load test)."""
        tools = ["status", "gap_to", "tyre_status", "damage_status", "pit_window", "pit_loss", "weather_change",
                 "tyre_field", "last_lap", "laps", "ai_deploy", "reminders"]
        while not stop.is_set():
            t = random.choice(tools)
            if t == "status":
                code, st, ms = _get("/status", self.host, self.port)
                self._log({"kind": "ask", "tool": "status", "code": code, "ms": ms, "error": st.get("error"),
                           "load": True})
                drivers = [n for n in (st.get("drivers") or []) if n != st.get("you")] or drivers
            elif t == "gap_to":
                if drivers:
                    self._ask("gap_to", driver=random.choice(list(drivers)))
            else:
                self._ask(t)

    def _weather_watch(self, w):
        """Log once per forecast when it shows drier weather within DRYING_SOON_MIN minutes."""
        cur, to, mins = w.get("current_weather"), w.get("change_to"), w.get("change_at_minutes")
        if to is not None and cur is not None and to < cur and mins is not None and mins <= DRYING_SOON_MIN:
            key = (cur, to, mins)
            if key != self.last_weather_alert:
                self._log({"kind": "event", "event": "drying_soon", "from": w.get("current_name"),
                           "to": to, "in_minutes": mins, "rain_pct_peak": w.get("peak_rain_pct")})
                self.last_weather_alert = key

    def _tyre_watch(self, tf):
        """Log every change in how many other cars are on dry tyres, and what you are on."""
        n = tf.get("others_on_dry")
        if n != self.last_dry_count:
            self._log({"kind": "event", "event": "field_tyres", "others_on_dry": n,
                       "others_total": tf.get("others_total"), "yours": tf.get("yours"), "others": tf.get("others")})
            self.last_dry_count = n


def run(every=3.0, minutes=150.0, log_path=None):
    log_path = log_path or RUN / f"drill_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl"
    d = Drill(log_path)
    d._log({"kind": "start", "every_s": every, "minutes": minutes})
    end = time.time() + minutes * 60
    print(f"drill: logging to {log_path}; stop with {STOP} or after {minutes} min", flush=True)
    while time.time() < end and not STOP.exists():
        t0 = time.time()
        try:
            d.step()
        except Exception as e:                       # the drill must keep going and say what broke
            d._log({"kind": "event", "event": "drill_error", "detail": repr(e)[:300]})
        time.sleep(max(0.0, every - (time.time() - t0)))
    d._log({"kind": "stop", "reason": "stop file" if STOP.exists() else "time"})
    if STOP.exists():
        STOP.unlink()
    return log_path


def summary(path):
    with open(path, encoding="utf-8") as f:
        recs = [json.loads(l) for l in f if l.strip()]
    asks = [r for r in recs if r["kind"] == "ask"]
    events = [r for r in recs if r["kind"] == "event"]
    walls = [r["wall"] for r in recs]
    out = [f"Drill log: {path}", f"Duration: {(max(walls) - min(walls)) / 60:.1f} min, {len(asks)} questions asked.", "",
           "Per question: answered OK / asked | time ms (median, 95th %, max) | most common error"]
    for tool in sorted({r["tool"] for r in asks}):
        rs = [r for r in asks if r["tool"] == tool]
        ms = sorted(r["ms"] for r in rs)
        ok = sum(1 for r in rs if r["code"] == 200)
        errs = {}
        for r in rs:
            if r["code"] != 200:
                errs[r.get("error") or f"HTTP {r['code']}"] = errs.get(r.get("error") or f"HTTP {r['code']}", 0) + 1
        top = max(errs.items(), key=lambda x: x[1]) if errs else None
        p95 = ms[min(len(ms) - 1, int(round(0.95 * (len(ms) - 1))))]
        out.append(f"  {tool:<15} {ok}/{len(rs)} | {statistics.median(ms):.0f}, {p95:.0f}, {ms[-1]:.0f} ms"
                   + (f" | {top[1]}x \"{top[0]}\"" if top else ""))
    counts = {}
    for e in events:
        counts[e["event"]] = counts.get(e["event"], 0) + 1
    out += ["", "Events:"] + ([f"  {k}: {v}" for k, v in sorted(counts.items())] or ["  none"])
    delays = [e["delay_s"] for e in events if e["event"] == "lap_report"]
    if delays:
        out.append(f"Lap reports: {len(delays)} found, seen {min(delays)}-{max(delays)} s after the lap number changed "
                   f"(includes up to one drill interval of waiting).")
    for e in events:
        if e["event"] == "drying_soon":
            out.append(f"  Drying forecast at {e['wall']:.0f}: from {e['from']} to code {e['to']} in {e['in_minutes']} min")
        if e["event"] == "field_tyres":
            out.append(f"  Tyres at {e['wall']:.0f}: you on {e['yours']}, {e['others_on_dry']}/{e['others_total']} others on dry")
    missing = [e["lap"] for e in events if e["event"] == "lap_report_missing"]
    if missing:
        out.append(f"Laps with NO report within {LAP_REPORT_WAIT_S} s: {missing}")
    errs = [e for e in events if e["event"] == "drill_error"]
    if errs:
        out.append(f"Drill errors (the drill itself): {len(errs)}, first: {errs[0]['detail']}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--every", type=float, default=3.0)
    r.add_argument("--minutes", type=float, default=150.0)
    s = sub.add_parser("summary")
    s.add_argument("path", nargs="?")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.every, a.minutes)
    else:
        path = a.path or max(RUN.glob("drill_*.jsonl"), key=lambda p: p.stat().st_mtime, default=None)
        if not path:
            raise SystemExit("No drill log found in run/.")
        print(summary(path))


if __name__ == "__main__":
    main()
