"""Session digest: read a session's parsed.jsonl ONCE and keep everything the reports need, in a small
file next to it (digest.pkl.gz). Later reports and questions load the digest in seconds.

  python -m race_engineer.session_digest PATH/parsed.jsonl      (builds or reuses the digest, prints a summary)

Kept:
- meta: track, session type, total laps, track length, weekend link id, AI difficulty, names, your slot
- every car's laps (same rules as the lap tools: session_reader.LapBuilder) with samples
  (distance, lap time, speed, throttle, brake, gear, steer), battery samples, fuel at lap start,
  and for YOUR car the track position (Motion worldPositionX / Z)
- the latest car setups, weather and safety car changes, and race events (events.LISTED codes)
The digest is rebuilt if parsed.jsonl is newer or DIGEST_VERSION changes. Read-only on parsed.jsonl.
"""
import argparse
import gzip
import json
import pickle
from pathlib import Path

from .ers_analysis import ErsLapBuilder
from .events import LISTED

DIGEST_NAME = "digest.pkl.gz"
DIGEST_VERSION = 2   # 2: events and safety car changes carry your lap number
FRAMED = {"LapData": "lapData", "CarTelemetry": "carTelemetryData", "CarStatus": "carStatusData",
          "Motion": "carMotionData"}
KEYS = tuple(f'"packet_name": "{k}"' for k in list(FRAMED) + ["Participants", "Session", "CarSetups", "Event"])
WINDOW = 64


class _Builder(ErsLapBuilder):
    def add_all(self, ld, tel, st, mo, keep_pos):
        before = self.prev_key
        self.add_with_status(ld, tel, st)
        if ld["lapDistance"] < 0:
            return
        lap = self.laps[self.prev_key]
        if self.prev_key != before or "fuel_start" not in lap:
            lap["fuel_start"] = st.get("fuelInTank")
        lap["fuel_last"] = st.get("fuelInTank")
        lap["mix"] = st.get("fuelMix")
        if keep_pos and mo is not None:
            lap.setdefault("pos", []).append((ld["lapDistance"], mo.get("worldPositionX"), mo.get("worldPositionZ")))


def build(path):
    meta = {"names": [], "player": None, "track_id": None, "session_type": None, "total_laps": None,
            "track_length": None, "weekend_id": None, "ai_difficulty": None, "n": 0}
    builders = []
    setups = None
    weather, sc, events = [], [], []
    pend = {k: {} for k in FRAMED}
    session_time = None
    my_lap = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not any(k in line[:120] for k in KEYS):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            kind = d["packet_name"]
            session_time = d.get("sessionTime", session_time)
            if kind == "Participants":
                n = max(d.get("numActiveCars", len(d["participants"])), meta["n"])   # never shrink
                parts = d["participants"][:n]
                meta["names"] = [p["name"] for p in parts]
                meta["player"] = next((i for i, p in enumerate(parts) if p.get("aiControlled") == 0), meta["player"])
                if n > meta["n"]:
                    builders += [_Builder() for _ in range(n - meta["n"])]
                    meta["n"] = n
            elif kind == "Session":
                for k_out, k_in in (("track_id", "trackId"), ("session_type", "sessionType"), ("total_laps", "totalLaps"),
                                    ("track_length", "trackLength"), ("weekend_id", "weekendLinkIdentifier"),
                                    ("ai_difficulty", "aiDifficulty")):
                    if d.get(k_in) is not None:
                        meta[k_out] = d[k_in]
                w = (d.get("weather"), d.get("trackTemperature"), d.get("airTemperature"))
                if not weather or weather[-1][1:] != w:
                    weather.append((session_time,) + w)
                s = d.get("safetyCarStatus")
                if s is not None and (not sc or sc[-1][1] != s):
                    sc.append((session_time, s, my_lap))
            elif kind == "CarSetups":
                setups = d.get("carSetupData")
            elif kind == "Event":
                if d.get("eventCode") in LISTED:
                    events.append((session_time, d.get("eventCode"), d.get("details"), my_lap))
            elif kind in FRAMED and builders:
                fr = d.get("frameIdentifier")
                pend[kind][fr] = d[FRAMED[kind]]
                if all(fr in p for p in pend.values()):
                    lap, tel, st, mo = (pend[k].pop(fr) for k in FRAMED)
                    if meta["player"] is not None and meta["player"] < len(lap):
                        my_lap = lap[meta["player"]]["currentLapNum"]
                    for slot in range(min(meta["n"], len(lap), len(tel), len(st))):
                        builders[slot].add_all(lap[slot], tel[slot], st[slot],
                                               mo[slot] if slot < len(mo) else None, slot == meta["player"])
                if len(pend[kind]) > WINDOW:
                    pend[kind].pop(next(iter(pend[kind])))
    if meta["names"] and setups:
        setups = setups[: meta["n"]]
    if not meta["track_length"]:
        meta["track_length"] = max((s[0] for b in builders for l in b.laps.values() for s in l["samples"]), default=0)
    return {"version": DIGEST_VERSION, "meta": meta, "laps": [b.laps for b in builders], "setups": setups,
            "weather": weather, "safety_car": sc, "events": events}


def load(path, rebuild=False):
    """Return (digest, from_cache)."""
    path = Path(path)
    cache = path.with_name(DIGEST_NAME)
    if not rebuild and cache.is_file() and cache.stat().st_mtime >= path.stat().st_mtime:
        try:
            with gzip.open(cache, "rb") as f:
                d = pickle.load(f)
            if d.get("version") == DIGEST_VERSION:
                return d, True
        except (OSError, EOFError, pickle.UnpicklingError, KeyError):
            pass
    d = build(path)
    try:
        with gzip.open(cache, "wb", compresslevel=3) as f:
            pickle.dump(d, f, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass
    return d, False


def as_compare_data(d):
    """The shape compare.analyse expects."""
    m = d["meta"]
    return {"names": m["names"], "player": m["player"], "track_length": m["track_length"],
            "setups": d["setups"], "laps": d["laps"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args(argv)
    d, cached = load(a.path, a.rebuild)
    m = d["meta"]
    laps = sum(len(x) for x in d["laps"])
    print(f"{'Loaded digest' if cached else 'Built digest'}: track {m['track_id']}, session type {m['session_type']}, "
          f"{m['n']} cars, {laps} laps, {len(d['events'])} events, safety car changes {len(d['safety_car'])}.")


if __name__ == "__main__":
    main()
