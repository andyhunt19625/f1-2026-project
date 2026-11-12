"""Battery (ERS) use: where on the lap you spend and gain energy, against the quickest other car.

  python -m race_engineer.ers_analysis PATH/parsed.jsonl [--segment-m 25] [--json]

Uses, from the saved session (read-only):
- CarStatus: ersStoreEnergy (J), enginePowerMGUK (W), ersDeployMode, ersDeployedThisLap (J),
  ersHarvestedThisLapMGUK / MGUH (J), ersHarvestLimitPerLap (J, 2026 format only).
- LapData + CarTelemetry for distance, lap time, speed, throttle, steering.
All three are joined by frameIdentifier in file order. Laps are split exactly as in the lap
tools (session_reader.LapBuilder) and only clean complete laps are used.

What is measured:
- "net battery" for a piece of track = stored energy at its end minus at its start
  (negative = energy taken out of the battery there, positive = energy put back).
- "MGU-K out" = MGU-K power above zero, multiplied by the time spent, summed over the piece.
- Track pieces are typed slow / medium / fast corner or straight from YOUR best lap, with the
  same conventions as compare.py (my thresholds, not the game's).
Nothing here says what the deploy modes mean; values are reported as the game sends them.
"""
import argparse
import json

from .compare import classify
from .lap_analysis import clean_series, complete, fmt_ms
from .session_reader import LapBuilder, lap_label

KEYS = ('"packet_name": "Participants"', '"packet_name": "LapData"', '"packet_name": "CarTelemetry"',
        '"packet_name": "CarStatus"', '"packet_name": "Session"')
WINDOW = 64
TYPES = ("slow", "medium", "fast", "straight")


class ErsLapBuilder(LapBuilder):
    """LapBuilder that also keeps, per sample, (dist, t_ms, store_J, mguk_W, deploy_mode)
    and the lap's last deployed / harvested totals."""

    def add_with_status(self, ld, tel, st):
        self.add(ld, tel)
        if ld["lapDistance"] < 0:
            return
        lap = self.laps[self.prev_key]
        lap.setdefault("ers", []).append((ld["lapDistance"], ld["currentLapTimeInMS"],
                                          st.get("ersStoreEnergy"), st.get("enginePowerMGUK"),
                                          st.get("ersDeployMode")))
        lap["deployed_J"] = st.get("ersDeployedThisLap")
        lap["harvested_J"] = (st.get("ersHarvestedThisLapMGUK") or 0) + (st.get("ersHarvestedThisLapMGUH") or 0)
        lap["harvest_limit_J"] = st.get("ersHarvestLimitPerLap")


def read_frames(path):
    """Yield ('participants', d) | ('session', d) | ('frame', lap, tel, status) in file order."""
    pend = {"LapData": {}, "CarTelemetry": {}, "CarStatus": {}}
    field = {"LapData": "lapData", "CarTelemetry": "carTelemetryData", "CarStatus": "carStatusData"}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not any(k in line[:120] for k in KEYS):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            kind = d["packet_name"]
            if kind == "Participants":
                yield "participants", d
            elif kind == "Session":
                yield "session", d
            else:
                fr = d.get("frameIdentifier")
                pend[kind][fr] = d[field[kind]]
                if all(fr in p for p in pend.values()):
                    yield ("frame", pend["LapData"].pop(fr), pend["CarTelemetry"].pop(fr),
                           pend["CarStatus"].pop(fr))
                if len(pend[kind]) > WINDOW:
                    pend[kind].pop(next(iter(pend[kind])))


def load(path):
    names = n = player = length = None
    builders = None
    for ev in read_frames(path):
        if ev[0] == "participants" and names is None:
            d = ev[1]
            n = d.get("numActiveCars", len(d["participants"]))
            parts = d["participants"][:n]
            names = [p["name"] for p in parts]
            player = next((i for i, p in enumerate(parts) if p.get("aiControlled") == 0), None)
            builders = [ErsLapBuilder() for _ in range(n)]
        elif ev[0] == "session" and length is None and ev[1].get("trackLength"):
            length = ev[1]["trackLength"]
        elif ev[0] == "frame" and builders:
            _, lap, tel, st = ev
            for slot in range(min(n, len(lap), len(tel), len(st))):
                builders[slot].add_with_status(lap[slot], tel[slot], st[slot])
    if names is None:
        raise SystemExit("No Participants packet in this session.")
    if not length:
        length = max((s[0] for b in builders for l in b.laps.values() for s in l["samples"]), default=0)
    return {"names": names, "player": player, "track_length": length, "laps": [b.laps for b in builders]}


def ers_series(lap):
    out = []
    for e in sorted(lap.get("ers", []), key=lambda x: (x[0], x[1])):
        if e[2] is None:
            continue
        if not out or (e[0] > out[-1][0] and e[1] >= out[-1][1]):
            out.append(e)
    return out


def store_at(series, d):
    """Stored energy (J) at distance d, linear between samples."""
    if not series:
        return None
    if d <= series[0][0]:
        return series[0][2]
    for a, b in zip(series, series[1:]):
        if a[0] <= d <= b[0]:
            return a[2] + (b[2] - a[2]) * (d - a[0]) / (b[0] - a[0]) if b[0] > a[0] else a[2]
    return series[-1][2]


def mguk_out_J(series, lo, hi):
    tot = 0.0
    for a, b in zip(series, series[1:]):
        if lo <= a[0] < hi and (a[3] or 0) > 0:
            tot += a[3] * max(0, b[1] - a[1]) / 1000.0
    return tot


def lap_summary(lap):
    s = ers_series(lap)
    if not s:
        return None
    lo = min(s, key=lambda e: e[2])
    modes = {}
    for a, b in zip(s, s[1:]):
        modes[a[4]] = modes.get(a[4], 0) + max(0, b[1] - a[1])
    total = sum(modes.values()) or 1
    return {"store_start_MJ": s[0][2] / 1e6, "store_end_MJ": s[-1][2] / 1e6,
            "store_min_MJ": lo[2] / 1e6, "store_min_at_m": round(lo[0]),
            "deployed_MJ": (lap.get("deployed_J") or 0) / 1e6, "harvested_MJ": (lap.get("harvested_J") or 0) / 1e6,
            "harvest_limit_MJ": (lap["harvest_limit_J"] / 1e6) if lap.get("harvest_limit_J") else None,
            "deploy_mode_share": {m: round(100 * t / total) for m, t in sorted(modes.items(), key=lambda x: str(x[0]))}}


def pieces_from(best, length, seg_m):
    series = clean_series(best["samples"])
    out, lo = [], 0.0
    while lo < length:
        hi = min(lo + seg_m, length)
        out.append((lo, hi, classify(series, lo, hi, 0.05, 120, 200)[0]))
        lo = hi
    return out


def sections(pieces):
    """Join neighbouring pieces of the same type into sections."""
    out = []
    for lo, hi, t in pieces:
        if out and out[-1][2] == t:
            out[-1][1] = hi
        else:
            out.append([lo, hi, t])
    return out


def top_speed(lap, lo, hi):
    v = [s[2] for s in lap["samples"] if lo <= s[0] < hi]
    return max(v) if v else None


def analyse(data, seg_m=25):
    length, me = data["track_length"], data["player"]
    good = {k: l for k, l in data["laps"][me].items() if complete(l, length) and l.get("ers")}
    if not good:
        return {"error": "No clean complete lap with battery data for you in this session."}
    best_k = min(good, key=lambda k: good[k]["time_ms"])
    best = good[best_k]
    other = None
    for slot, laps in enumerate(data["laps"]):
        if slot == me:
            continue
        g = {k: l for k, l in laps.items() if complete(l, length) and l.get("ers")}
        if g:
            k = min(g, key=lambda x: g[x]["time_ms"])
            if other is None or g[k]["time_ms"] < other[2]["time_ms"]:
                other = (slot, k, g[k])
    pieces = pieces_from(best, length, seg_m)
    mine = ers_series(best)
    theirs = ers_series(other[2]) if other else []

    def net(series, lo, hi):
        a, b = store_at(series, lo), store_at(series, hi)
        return None if a is None or b is None else b - a

    by_type = {t: {"you_net_MJ": 0.0, "them_net_MJ": 0.0, "you_mguk_MJ": 0.0, "them_mguk_MJ": 0.0} for t in TYPES}
    for lo, hi, t in pieces:
        if t not in by_type:
            continue
        by_type[t]["you_net_MJ"] += (net(mine, lo, hi) or 0) / 1e6
        by_type[t]["you_mguk_MJ"] += mguk_out_J(mine, lo, hi) / 1e6
        if theirs:
            by_type[t]["them_net_MJ"] += (net(theirs, lo, hi) or 0) / 1e6
            by_type[t]["them_mguk_MJ"] += mguk_out_J(theirs, lo, hi) / 1e6
    secs = []
    for lo, hi, t in sections(pieces):
        if t == "unknown":
            continue
        row = {"from_m": round(lo), "to_m": round(hi), "type": t,
               "you_net_MJ": (net(mine, lo, hi) or 0) / 1e6, "you_mguk_MJ": mguk_out_J(mine, lo, hi) / 1e6,
               "you_top_kmh": top_speed(best, lo, hi)}
        if theirs:
            row.update({"them_net_MJ": (net(theirs, lo, hi) or 0) / 1e6,
                        "them_mguk_MJ": mguk_out_J(theirs, lo, hi) / 1e6,
                        "them_top_kmh": top_speed(other[2], lo, hi)})
        secs.append(row)
    return {"you": data["names"][me], "best_lap": lap_label(best_k), "best_ms": best["time_ms"],
            "laps": {lap_label(k): dict(lap_summary(l), time_ms=l["time_ms"]) for k, l in sorted(good.items())},
            "best_summary": lap_summary(best),
            "other": ({"driver": data["names"][other[0]], "lap": lap_label(other[1]), "ms": other[2]["time_ms"],
                       "summary": lap_summary(other[2])} if other else None),
            "by_type": by_type, "sections": secs}


def report(res):
    if "error" in res:
        return res["error"]
    o = res["other"]
    out = [f"Battery use. Your best clean lap: {res['best_lap']}, {fmt_ms(res['best_ms'])}."]
    if o:
        out.append(f"Compared with the quickest other car: {o['driver'].title()} ({o['lap']}, {fmt_ms(o['ms'])}).")
    out += ["", "Per lap (yours): start -> end store, lowest point, deployed, harvested, deploy mode share of lap time"]
    for n, s in res["laps"].items():
        out.append(f"  {n}: {fmt_ms(s['time_ms'])} | {s['store_start_MJ']:.2f} -> {s['store_end_MJ']:.2f} MJ | "
                   f"lowest {s['store_min_MJ']:.2f} MJ at {s['store_min_at_m']} m | deployed {s['deployed_MJ']:.2f} | "
                   f"harvested {s['harvested_MJ']:.2f} MJ | modes {s['deploy_mode_share']}")
    if o and o["summary"]:
        s = o["summary"]
        out.append(f"  {o['driver'].title()} best lap: {s['store_start_MJ']:.2f} -> {s['store_end_MJ']:.2f} MJ | "
                   f"lowest {s['store_min_MJ']:.2f} MJ at {s['store_min_at_m']} m | deployed {s['deployed_MJ']:.2f} | "
                   f"harvested {s['harvested_MJ']:.2f} MJ | modes {s['deploy_mode_share']}")
    out += ["", "Best lap, by type of track (net battery: minus = used there; MGU-K out = energy sent to the wheels):"]
    for t in TYPES:
        b = res["by_type"][t]
        line = f"  {t:<8} you net {b['you_net_MJ']:+.2f} MJ, MGU-K out {b['you_mguk_MJ']:.2f} MJ"
        if o:
            line += f" | {o['driver'].title()} net {b['them_net_MJ']:+.2f} MJ, MGU-K out {b['them_mguk_MJ']:.2f} MJ"
        out.append(line)
    out += ["", "Section by section on your best lap:"]
    for r in res["sections"]:
        line = (f"  {r['from_m']:>5}-{r['to_m']:<5} {r['type']:<8} you net {r['you_net_MJ']:+.2f} MJ "
                f"(MGU-K out {r['you_mguk_MJ']:.2f}), top {r['you_top_kmh']} km/h")
        if "them_net_MJ" in r:
            line += (f" | them net {r['them_net_MJ']:+.2f} MJ (MGU-K out {r['them_mguk_MJ']:.2f}), "
                     f"top {r['them_top_kmh']} km/h")
        out.append(line)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--segment-m", type=float, default=25.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = analyse(load(a.path), a.segment_m)
    print(json.dumps(res, indent=1) if a.json else report(res))


if __name__ == "__main__":
    main()
