"""Where am I losing time to the cars that are faster than me, in slow, medium and
fast corners and on the straights, and how do our setups differ?

  python3 -m race_engineer.compare PATH/parsed.jsonl [--segment-m 25] [--slow 120] [--fast 200]
                                   [--steer 0.05] [--max-cars 5] [--json]

Method (all from the saved session, read-only):
- Every car's laps are rebuilt from LapData + CarTelemetry joined by frameIdentifier.
  Laps with any pit status, invalid laps and incomplete laps are left out.
- Your fastest clean lap is compared with each faster car's fastest clean lap, in
  pieces of --segment-m metres round the track.
- A piece is a STRAIGHT if your steering stays under --steer (absolute, the game's
  -1..1 scale) on your best lap; otherwise it is a corner, classed by your minimum speed
  there: SLOW below --slow km/h, FAST above --fast km/h, MEDIUM in between. These limits
  are conventions chosen for this tool, not values from the game; change them as you like.
- Setups come from the latest CarSetups packet in the session.
"""
import argparse
import json

from .lap_analysis import clean_series, complete, fmt_ms, time_at

KEYS = ('"packet_name": "Participants"', '"packet_name": "LapData"',
        '"packet_name": "CarTelemetry"', '"packet_name": "Session"',
        '"packet_name": "CarSetups"')
SETUP_FIELDS = ("frontWing", "rearWing", "onThrottle", "offThrottle", "frontCamber", "rearCamber",
                "frontToe", "rearToe", "frontSuspension", "rearSuspension", "frontAntiRollBar",
                "rearAntiRollBar", "frontSuspensionHeight", "rearSuspensionHeight", "brakePressure",
                "brakeBias", "engineBraking", "frontLeftTyrePressure", "frontRightTyrePressure",
                "rearLeftTyrePressure", "rearRightTyrePressure", "ballast", "fuelLoad")


def load_all(path):
    names, n, player, track_len, setups = None, None, None, None, None
    lapd, tel = {}, {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            head = line[:120]
            if not any(k in head for k in KEYS):
                continue
            d = json.loads(line)
            kind = d["packet_name"]
            if kind == "Participants" and names is None:
                n = d.get("numActiveCars", len(d["participants"]))
                parts = d["participants"][:n]
                names = [p["name"] for p in parts]
                player = next((i for i, p in enumerate(parts) if p.get("aiControlled") == 0), None)
            elif kind == "Session" and track_len is None and d.get("trackLength"):
                track_len = d["trackLength"]
            elif kind == "CarSetups" and n:
                setups = d["carSetupData"][:n]
            elif kind == "LapData" and n:
                lapd[d["frameIdentifier"]] = [
                    (c["lapDistance"], c["currentLapTimeInMS"], c["currentLapNum"],
                     c["currentLapInvalid"], c["pitStatus"], c["lastLapTimeInMS"])
                    for c in d["lapData"][:n]]
            elif kind == "CarTelemetry" and n:
                tel[d["frameIdentifier"]] = [
                    (c["speed"], c["throttle"], c["brake"], c["gear"], c["steer"])
                    for c in d["carTelemetryData"][:n]]
    if names is None:
        raise SystemExit("No Participants packet in this session.")
    laps = [build_laps(lapd, tel, slot) for slot in range(n)]
    return {"names": names, "player": player, "track_length": track_len, "setups": setups, "laps": laps}


def build_laps(lapd, tel, slot):
    laps, prev = {}, None
    for frame in sorted(lapd):
        dist, t_ms, num, invalid, pit, last_ms = lapd[frame][slot]
        lap = laps.setdefault(num, {"samples": [], "time_ms": None, "valid": True, "pit": False})
        if prev is not None and num != prev and prev in laps:
            laps[prev]["time_ms"] = last_ms or None
        prev = num
        lap["valid"] = lap["valid"] and not invalid
        lap["pit"] = lap["pit"] or bool(pit)
        tl = tel.get(frame)
        if tl is None or dist < 0:
            continue
        speed, throttle, brake, gear, steer = tl[slot]
        lap["samples"].append((dist, t_ms, speed, throttle, brake, gear, steer))
    return laps


def best_lap(laps, length):
    good = {k: v for k, v in laps.items() if complete(v, length)}
    if not good:
        return None, None
    k = min(good, key=lambda x: good[x]["time_ms"])
    return k, good[k]


def classify(series, lo, hi, steer_thr, slow, fast):
    pts = [s for s in series if lo <= s[0] < hi]
    if not pts:
        return "unknown", None
    min_speed = min(s[2] for s in pts)
    if max(abs(s[6]) for s in pts) < steer_thr:
        return "straight", min_speed
    if min_speed < slow:
        return "slow", min_speed
    if min_speed > fast:
        return "fast", min_speed
    return "medium", min_speed


def detail(series, lo, hi):
    pts = [s for s in series if lo <= s[0] < hi]
    if not pts:
        return {}
    apex = min(pts, key=lambda s: s[2])
    brake = next((s[0] for s in pts if s[4] > 0.1), None)
    throttle = next((s[0] for s in pts if s[0] >= apex[0] and s[3] > 0.95), None)
    return {"apex_speed": apex[2], "apex_at_m": round(apex[0]), "brake_at_m": round(brake) if brake else None,
            "full_throttle_at_m": round(throttle) if throttle else None,
            "top_speed": max(s[2] for s in pts)}


def analyse(data, seg_m=25, slow=120, fast=200, steer_thr=0.05, max_cars=5):
    length = data["track_length"] or 0
    me = data["player"]
    my_n, my_best = best_lap(data["laps"][me], length)
    if my_best is None:
        return {"error": "You have no clean complete lap in this session."}
    others = []
    for slot, laps in enumerate(data["laps"]):
        if slot == me:
            continue
        n, b = best_lap(laps, length)
        if b:
            others.append((b["time_ms"], slot, n, b))
    others.sort()
    faster = [o for o in others if o[0] < my_best["time_ms"]][:max_cars]
    note = None
    if not faster:
        faster = others[:1]
        note = "No car was faster than you; compared with the next quickest car instead."
    mine = clean_series(my_best["samples"])
    md = [s[0] for s in mine]
    # classify pieces of the track from your own best lap
    pieces = []
    lo = 0.0
    while lo < length:
        hi = min(lo + seg_m, length)
        cls, _ = classify(mine, lo, hi, steer_thr, slow, fast)
        pieces.append((lo, hi, cls))
        lo = hi
    # group consecutive corner pieces into corners
    corners, cur = [], None
    for lo, hi, cls in pieces:
        if cls in ("slow", "medium", "fast"):
            if cur is None:
                cur = [lo, hi]
            else:
                cur[1] = hi
        elif cur is not None:
            corners.append(cur)
            cur = None
    if cur is not None:
        corners.append(cur)
    results = []
    for t_ms, slot, n, b in faster:
        theirs = clean_series(b["samples"])
        td = [s[0] for s in theirs]

        def loss(lo, hi):
            return ((time_at(mine, md, hi) - time_at(theirs, td, hi))
                    - (time_at(mine, md, lo) - time_at(theirs, td, lo)))
        by_class = {"slow": 0.0, "medium": 0.0, "fast": 0.0, "straight": 0.0, "unknown": 0.0}
        for lo, hi, cls in pieces:
            by_class[cls] += loss(lo, hi)
        corner_list = []
        for i, (lo, hi) in enumerate(corners, 1):
            cls, _ = classify(mine, lo, hi, steer_thr, slow, fast)
            corner_list.append({"corner": i, "from_m": round(lo), "to_m": round(hi), "type": cls,
                                "loss_ms": round(loss(lo, hi)), "you": detail(mine, lo, hi),
                                "them": detail(theirs, lo, hi)})
        results.append({"driver": data["names"][slot], "best_lap_ms": t_ms, "lap": n,
                        "gap_ms": my_best["time_ms"] - t_ms,
                        "loss_by_type_ms": {k: round(v) for k, v in by_class.items() if k != "unknown" or v},
                        "corners": corner_list,
                        "setup_differences": setup_diff(data["setups"], me, slot)})
    summary = {}
    for r in results:
        for k, v in r["loss_by_type_ms"].items():
            summary.setdefault(k, []).append(v)
    return {"you": data["names"][me], "your_best_ms": my_best["time_ms"], "your_lap": my_n,
            "note": note, "limits": {"slow_below_kmh": slow, "fast_above_kmh": fast,
                                     "straight_if_steer_below": steer_thr, "piece_m": seg_m},
            "average_loss_by_type_ms": {k: round(sum(v) / len(v)) for k, v in summary.items()},
            "versus": results}


def setup_diff(setups, me, other):
    if not setups or me >= len(setups) or other >= len(setups):
        return None
    a, b = setups[me], setups[other]
    return {k: {"you": a.get(k), "them": b.get(k)} for k in SETUP_FIELDS if a.get(k) != b.get(k)}


GENERAL_GUIDANCE = {
    "slow": "Slow corners are usually about traction and rotation: mechanical grip (softer anti-roll "
            "bars or suspension), the on-throttle differential, and brake bias for entry.",
    "medium": "Medium corners are a balance of mechanical and aero grip: anti-roll bar balance and "
              "wing balance are the usual places to look.",
    "fast": "Fast corners are usually about aero grip and a stable platform: more wing, stiffer "
            "suspension, lower ride height.",
    "straight": "Straights are usually about drag and exits: less wing, or better traction out of the "
                "corner before the straight.",
}


def report(res, top=5):
    if "error" in res:
        return res["error"]
    lim = res["limits"]
    out = [f"Your best clean lap: lap {res['your_lap']}, {fmt_ms(res['your_best_ms'])}."]
    if res["note"]:
        out.append(res["note"])
    out.append(f"(Corner types: slow < {lim['slow_below_kmh']} km/h, fast > {lim['fast_above_kmh']} "
               f"km/h, straight if |steer| < {lim['straight_if_steer_below']}; my conventions.)")
    out += ["", "Average time lost to the faster cars, by type:"]
    avg = res["average_loss_by_type_ms"]
    for k in ("slow", "medium", "fast", "straight"):
        if k in avg:
            out.append(f"  {k:<8} {avg[k] / 1000:+.3f} s")
    worst = max((k for k in ("slow", "medium", "fast", "straight") if k in avg), key=lambda k: avg[k])
    for r in res["versus"]:
        out += ["", f"vs {r['driver'].title()} (lap {r['lap']}, {fmt_ms(r['best_lap_ms'])}, "
                    f"{r['gap_ms'] / 1000:.3f} s quicker):"]
        out.append("  by type: " + ", ".join(f"{k} {v / 1000:+.3f} s" for k, v in r["loss_by_type_ms"].items()))
        for c in sorted(r["corners"], key=lambda c: -c["loss_ms"])[:top]:
            y, t = c["you"], c["them"]
            out.append(f"  corner {c['corner']} ({c['type']}, {c['from_m']}-{c['to_m']} m): "
                       f"{c['loss_ms'] / 1000:+.3f} s | apex {y.get('apex_speed')} vs {t.get('apex_speed')} km/h"
                       f" | brake {y.get('brake_at_m')} vs {t.get('brake_at_m')} m"
                       f" | full throttle {y.get('full_throttle_at_m')} vs {t.get('full_throttle_at_m')} m")
        sd = r["setup_differences"]
        if sd is None:
            out.append("  setups: not available in this session")
        elif not sd:
            out.append("  setups: identical to yours")
        else:
            out.append("  setup differences (you vs them): " +
                       ", ".join(f"{k} {v['you']} vs {v['them']}" for k, v in sd.items()))
    out += ["", f"Biggest average loss is in {worst} sections.",
            "General setup direction (engineering rule of thumb, NOT measured from your data): "
            + GENERAL_GUIDANCE[worst],
            "Note: AI cars' speed comes mostly from the AI difficulty, and their setups may be generic, "
            "so their setup values are a comparison, not a recommendation."]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--segment-m", type=float, default=25.0)
    ap.add_argument("--slow", type=float, default=120.0)
    ap.add_argument("--fast", type=float, default=200.0)
    ap.add_argument("--steer", type=float, default=0.05)
    ap.add_argument("--max-cars", type=int, default=5)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    data = load_all(a.path)
    if not data["track_length"]:
        data["track_length"] = max((s[0] for laps in data["laps"] for l in laps.values()
                                    for s in l["samples"]), default=0)
    res = analyse(data, a.segment_m, a.slow, a.fast, a.steer, a.max_cars)
    print(json.dumps(res, indent=1) if a.json else report(res, a.top))


if __name__ == "__main__":
    main()
