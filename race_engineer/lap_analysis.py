"""Where do I lose time on the lap? Compares your clean laps against your fastest
clean lap, point by point round the track, from a saved session's parsed.jsonl.

  python3 -m race_engineer.lap_analysis PATH/parsed.jsonl [--driver NAME] [--segment-m 100] [--top 5]

Uses: LapData (lapDistance, currentLapTimeInMS, currentLapNum, currentLapInvalid,
pitStatus, lastLapTimeInMS) and CarTelemetry (speed, throttle, brake, gear), paired by
frameIdentifier in recorded order (see session_reader). Laps with any pit status, invalid
laps and incomplete laps are left out. Laps from separate runs are kept apart.
Read-only on the file.
"""
import argparse
import bisect
import json
import unicodedata

from .session_reader import LapBuilder, lap_label, read_events


def _norm(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def load(path, driver=None):
    """Return (laps, track_length, name). laps: {(run, lap_num): {"samples": [...],
    "time_ms", "valid", "pit"}}; samples are (dist, t_ms, speed, throttle, brake, gear, steer)."""
    slot = name = track_len = None
    builder = LapBuilder()
    for ev in read_events(path):
        kind = ev[0]
        if kind == "participants" and slot is None:
            d = ev[1]
            parts = d["participants"][: d.get("numActiveCars", len(d["participants"]))]
            for i, p in enumerate(parts):
                if (driver and _norm(driver) in _norm(p["name"]).split()) or \
                   (not driver and p.get("aiControlled") == 0):
                    slot, name = i, p["name"]
                    break
        elif kind == "session" and track_len is None and ev[1].get("trackLength"):
            track_len = ev[1]["trackLength"]
        elif kind == "frame" and slot is not None:
            _, _, lap, tel = ev
            if slot < len(lap) and slot < len(tel):
                builder.add(lap[slot], tel[slot])
    if slot is None:
        raise SystemExit("Could not find the driver in the Participants packet.")
    return builder.laps, track_len, name


def clean_series(samples):
    """Sort by distance and keep strictly increasing distance and time."""
    out = []
    for s in sorted(samples):
        if not out or (s[0] > out[-1][0] and s[1] >= out[-1][1]):
            out.append(s)
    return out


def time_at(series, dists, d):
    i = bisect.bisect_left(dists, d)
    if i <= 0:
        return series[0][1]
    if i >= len(series):
        return series[-1][1]
    (d0, t0), (d1, t1) = series[i - 1][:2], series[i][:2]
    return t0 + (t1 - t0) * (d - d0) / (d1 - d0) if d1 > d0 else t0


def segment_detail(series, lo, hi):
    pts = [s for s in series if lo <= s[0] < hi]
    if not pts:
        return None
    brake = next((round(s[0]) for s in pts if s[4] > 0.1), None)
    apex = min(pts, key=lambda s: s[2])
    throttle_on = next((round(s[0]) for s in pts if s[0] >= apex[0] and s[3] > 0.95), None)
    return {"min_speed": apex[2], "brake_at_m": brake, "full_throttle_at_m": throttle_on}


def compare(lap, ref, length, seg_m):
    a, r = clean_series(lap["samples"]), clean_series(ref["samples"])
    ad, rd = [s[0] for s in a], [s[0] for s in r]
    segs = []
    lo = 0.0
    while lo < length:
        hi = min(lo + seg_m, length)
        loss = ((time_at(a, ad, hi) - time_at(r, rd, hi)) - (time_at(a, ad, lo) - time_at(r, rd, lo)))
        segs.append({"from_m": round(lo), "to_m": round(hi), "loss_ms": round(loss),
                     "you": segment_detail(a, lo, hi), "best": segment_detail(r, lo, hi)})
        lo = hi
    return segs


def complete(lap, length):
    s = lap["samples"]
    return bool(lap["time_ms"] and lap["valid"] and not lap["pit"] and s
                and min(x[0] for x in s) < 0.05 * length and max(x[0] for x in s) > 0.95 * length)


def analyse(laps, length, seg_m=100):
    good = {k: l for k, l in laps.items() if complete(l, length)}
    if len(good) < 2:
        return {"error": f"Need at least 2 clean complete laps; found {len(good)}.",
                "laps": {lap_label(k): {"time_ms": l["time_ms"], "valid": l["valid"], "pit": l["pit"]}
                         for k, l in sorted(laps.items())}}
    best_k = min(good, key=lambda k: good[k]["time_ms"])
    per_lap, totals = {}, {}
    for k, l in sorted(good.items()):
        if k == best_k:
            continue
        segs = compare(l, good[best_k], length, seg_m)
        per_lap[lap_label(k)] = {"time_ms": l["time_ms"], "gap_to_best_ms": l["time_ms"] - good[best_k]["time_ms"],
                                 "segments": segs}
        for s in segs:
            totals.setdefault((s["from_m"], s["to_m"]), []).append(s["loss_ms"])
    avg = sorted(({"from_m": k[0], "to_m": k[1], "avg_loss_ms": round(sum(v) / len(v)), "laps": len(v)}
                  for k, v in totals.items()), key=lambda x: -x["avg_loss_ms"])
    return {"best_lap": lap_label(best_k), "best_time_ms": good[best_k]["time_ms"],
            "clean_laps": [lap_label(k) for k in sorted(good)],
            "all_laps": {lap_label(k): {"time_ms": l["time_ms"], "valid": l["valid"], "pit": l["pit"]}
                         for k, l in sorted(laps.items())},
            "per_lap": per_lap, "average_loss_by_segment": avg}


def fmt_ms(ms):
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


def _lap_line(label, v):
    t = fmt_ms(v["time_ms"]) if v["time_ms"] else "-"
    return f"  {label}: {t}{'' if v['valid'] else ' INVALID'}{' PIT' if v['pit'] else ''}"


def report(res, top=5):
    if "error" in res:
        return "\n".join([res["error"], "Laps seen:"] + [_lap_line(k, v) for k, v in res["laps"].items()])
    out = ["All laps:"] + [_lap_line(k, v) for k, v in res["all_laps"].items()]
    out += ["", f"Best clean lap: {res['best_lap']} in {fmt_ms(res['best_time_ms'])}. "
                f"Clean laps: {', '.join(res['clean_laps'])}.", "",
            f"Where you lose time on average (vs {res['best_lap']}):"]
    for s in res["average_loss_by_segment"][:top]:
        out.append(f"  {s['from_m']:>5}-{s['to_m']:<5} m: {s['avg_loss_ms'] / 1000:+.3f} s over {s['laps']} lap(s)")
    for n, l in res["per_lap"].items():
        out += ["", f"{n}: {fmt_ms(l['time_ms'])} (+{l['gap_to_best_ms'] / 1000:.3f} s). Biggest losses:"]
        total = sum(s["loss_ms"] for s in l["segments"])
        if abs(total - l["gap_to_best_ms"]) > 50:
            out.append(f"  WARNING: the pieces add up to {total / 1000:+.3f} s, not the real gap; "
                       f"this lap is NOT reliable.")
        for s in sorted(l["segments"], key=lambda x: -x["loss_ms"])[:3]:
            y, b = s["you"] or {}, s["best"] or {}
            out.append(f"  {s['from_m']}-{s['to_m']} m: {s['loss_ms'] / 1000:+.3f} s | min speed "
                       f"{y.get('min_speed')} vs {b.get('min_speed')} km/h | brake at "
                       f"{y.get('brake_at_m')} vs {b.get('brake_at_m')} m | full throttle at "
                       f"{y.get('full_throttle_at_m')} vs {b.get('full_throttle_at_m')} m")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--driver", default=None, help="default: your car (aiControlled = 0)")
    ap.add_argument("--segment-m", type=float, default=100.0)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    laps, length, name = load(a.path, a.driver)
    if not length:
        length = max((s[0] for l in laps.values() for s in l["samples"]), default=0)
    res = analyse(laps, length, a.segment_m)
    print(f"Driver: {name}. Track length: {length} m.")
    print(json.dumps(res, indent=1) if a.json else report(res, a.top))


if __name__ == "__main__":
    main()
