"""Lap analysis while you drive. Fed every decoded packet by LiveState; when you cross the
line it writes a short report of the lap you just finished:
  lap time and gap to your best clean lap, the 3 places you lost most against it (100 m pieces),
  battery: start -> end, used vs harvested, lowest point, where you spent most,
  and the gap to the quickest AI lap so far.
Reports are kept in memory (server tools last_lap / laps / ai_deploy) and appended to
run/live_laps.txt in the engineer folder.

Same lap rules as the saved-session tools (session_reader.LapBuilder, lap_analysis.complete).
To keep memory small over a long session, AI cars keep only their best clean lap and the
lap in progress; your laps are all kept.
"""
import os
from pathlib import Path

from . import ers_analysis as ers
from .lap_analysis import compare, complete, fmt_ms
from .session_reader import lap_label

LOG = Path(__file__).resolve().parent.parent / "run" / "live_laps.txt"
FIELDS = {"LapData": "lapData", "CarTelemetry": "carTelemetryData", "CarStatus": "carStatusData"}
WINDOW = 64


class _Builder(ers.ErsLapBuilder):
    def feed(self, ld, tel, st):
        """Add one sample; return the key of a lap that has just been given its time, or None."""
        before = self.prev_key
        self.add_with_status(ld, tel, st)
        if before is not None and before != self.prev_key and before in self.laps \
                and self.laps[before]["time_ms"]:
            return before
        return None


class LiveLaps:
    def __init__(self, log_path=LOG):
        self.log_path = log_path
        self.names, self.n, self.player, self.length = [], 0, None, None
        self.builders = []
        self.pend = {k: {} for k in FIELDS}
        self.reports = []

    # ---- input ----------------------------------------------------------
    def ingest(self, pkt):
        name = pkt.get("packet_name")
        if name == "Participants":
            n = pkt.get("numActiveCars", len(pkt["participants"]))
            parts = pkt["participants"][:n]
            self.names = [p["name"] for p in parts]
            self.player = next((i for i, p in enumerate(parts) if p.get("aiControlled") == 0), None)
            if n != self.n:
                self.n = n
                self.builders = [_Builder() for _ in range(n)]
        elif name == "Session":
            if pkt.get("trackLength"):
                self.length = pkt["trackLength"]
        elif name in FIELDS:
            fr = pkt.get("frameIdentifier")
            self.pend[name][fr] = pkt[FIELDS[name]]
            if all(fr in p for p in self.pend.values()):
                self._frame(*(self.pend[k].pop(fr) for k in FIELDS))
            if len(self.pend[name]) > WINDOW:
                self.pend[name].pop(next(iter(self.pend[name])))

    def _frame(self, lap, tel, st):
        if not self.builders or not self.length:
            return
        for slot in range(min(self.n, len(lap), len(tel), len(st))):
            done = self.builders[slot].feed(lap[slot], tel[slot], st[slot])
            if done is None:
                continue
            if slot == self.player:
                self._report(done)
            else:
                self._prune(slot)

    def _prune(self, slot):
        """AI car: keep only its best clean lap and the lap in progress."""
        b = self.builders[slot]
        best_k, _ = ers.best_clean(b.laps, self.length)                       # quickest lap (for lap times)
        active_k, _ = ers.best_clean(b.laps, self.length, need_active=True)   # quickest with real battery data
        for k in list(b.laps):
            if k not in (best_k, active_k, b.prev_key):
                del b.laps[k]

    # ---- analysis -------------------------------------------------------
    def _data(self):
        return {"names": self.names, "player": self.player, "track_length": self.length,
                "laps": [b.laps for b in self.builders]}

    def _report(self, key):
        laps = self.builders[self.player].laps
        lap = laps[key]
        label = lap_label(key)
        clean = complete(lap, self.length)
        lines = [f"{label}: {fmt_ms(lap['time_ms'])}" + ("" if clean else " (not counted: invalid, pit or incomplete)")]
        res = {"lap": label, "time_ms": lap["time_ms"], "clean": clean}
        earlier = {k: l for k, l in laps.items() if k != key and complete(l, self.length)}
        if clean and earlier:
            bk = min(earlier, key=lambda k: earlier[k]["time_ms"])
            gap = lap["time_ms"] - earlier[bk]["time_ms"]
            res["gap_to_best_ms"] = gap
            if gap < 0:
                lines.append(f"  New best, {-gap / 1000:.3f} s quicker than {lap_label(bk)}.")
            else:
                segs = sorted(compare(lap, earlier[bk], self.length, 100), key=lambda s: -s["loss_ms"])[:3]
                res["losses"] = segs
                lines.append(f"  +{gap / 1000:.3f} s to your best ({lap_label(bk)}). Most lost: " + "; ".join(
                    f"{s['from_m']}-{s['to_m']} m {s['loss_ms'] / 1000:+.3f} s (min speed "
                    f"{(s['you'] or {}).get('min_speed')} vs {(s['best'] or {}).get('min_speed')})" for s in segs))
        elif clean:
            lines.append("  First clean lap: nothing to compare with yet.")
        s = ers.lap_summary(lap)
        if s:
            res["battery"] = s
            net = s["store_end_MJ"] - s["store_start_MJ"]
            series = ers.ers_series(lap)
            spends = []
            lo = 0.0
            while lo < self.length:
                hi = min(lo + 100, self.length)
                a, b = ers.store_at(series, lo), ers.store_at(series, hi)
                if a is not None and b is not None:
                    spends.append((b - a, round(lo), round(hi)))
                lo = hi
            top = sorted(spends)[:2]
            lines.append(f"  Battery {s['store_start_MJ']:.2f} -> {s['store_end_MJ']:.2f} MJ ({net:+.2f}); used "
                         f"{s['deployed_MJ']:.2f}, harvested {s['harvested_MJ']:.2f}; lowest {s['store_min_MJ']:.2f} MJ "
                         f"at {s['store_min_at_m']} m. Most spent: " +
                         ", ".join(f"{f}-{t} m ({d / 1e6:+.2f})" for d, f, t in top))
        ai = None
        for slot, b in enumerate(self.builders):
            if slot == self.player:
                continue
            k, l = ers.best_clean(b.laps, self.length)
            if l and (ai is None or l["time_ms"] < ai[1]):
                ai = (self.names[slot], l["time_ms"])
        if ai and clean:
            d = lap["time_ms"] - ai[1]
            res["vs_ai"] = {"driver": ai[0], "ms": ai[1], "gap_ms": d}
            lines.append(f"  Quickest AI so far: {ai[0].title()} {fmt_ms(ai[1])} "
                         f"({'you are ' + format(-d / 1000, '.3f') + ' s quicker' if d < 0 else format(d / 1000, '+.3f') + ' s to you'}).")
        res["text"] = "\n".join(lines)
        self.reports.append(res)
        self._log(res["text"])

    def _log(self, text):
        try:
            os.makedirs(self.log_path.parent, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(text + "\n\n")
        except OSError:
            pass

    # ---- questions ------------------------------------------------------
    def last(self):
        if not self.reports:
            return {"answer": "No lap finished yet in this session."}
        return dict(self.reports[-1], answer=self.reports[-1]["text"])

    def all(self):
        return {"answer": "\n\n".join(r["text"] for r in self.reports) or "No lap finished yet in this session.",
                "count": len(self.reports)}

    def ai_deploy(self):
        if not self.builders or not self.length:
            return {"answer": "No session data yet."}
        res = ers.ai_deploy(self._data())
        return dict(res, answer=ers.ai_deploy_report(res))
