"""Track map data: the shape of the track from your car's position, with each piece coloured by
time gained or lost between two of your laps.

  python -m race_engineer.track_map PATH/parsed.jsonl --lap "lap 3 (from the line)" --ref "lap 2 (from the line)"
                                    [--segment-m 100] > map.json

Output JSON:
  points:   [[distance_m, x, z], ...] along --lap, about every 10 m (Motion worldPositionX / Z)
  segments: [{from_m, to_m, gain_ms, lap_min_kmh, ref_min_kmh}]   gain_ms > 0 = --lap was quicker there
  lap / ref labels and times.
Uses Motion + LapData + CarTelemetry joined by frameIdentifier in file order; laps split exactly
as the other lap tools. Read-only on the file.
"""
import argparse
import json

from .lap_analysis import compare
from .session_reader import LapBuilder, lap_label

KEYS = ('"packet_name": "Participants"', '"packet_name": "LapData"', '"packet_name": "CarTelemetry"',
        '"packet_name": "Motion"', '"packet_name": "Session"')
FIELDS = {"LapData": "lapData", "CarTelemetry": "carTelemetryData", "Motion": "carMotionData"}
WINDOW = 64


class _PosBuilder(LapBuilder):
    def add_pos(self, ld, tel, mo):
        self.add(ld, tel)
        if ld["lapDistance"] >= 0:
            self.laps[self.prev_key].setdefault("pos", []).append(
                (ld["lapDistance"], mo.get("worldPositionX"), mo.get("worldPositionZ")))


def load(path):
    slot = length = None
    b = _PosBuilder()
    pend = {k: {} for k in FIELDS}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not any(k in line[:120] for k in KEYS):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            kind = d["packet_name"]
            if kind == "Participants" and slot is None:
                parts = d["participants"][: d.get("numActiveCars", len(d["participants"]))]
                slot = next((i for i, p in enumerate(parts) if p.get("aiControlled") == 0), None)
            elif kind == "Session" and length is None and d.get("trackLength"):
                length = d["trackLength"]
            elif kind in FIELDS and slot is not None:
                fr = d.get("frameIdentifier")
                pend[kind][fr] = d[FIELDS[kind]]
                if all(fr in p for p in pend.values()):
                    lap, tel, mo = (pend[k].pop(fr) for k in FIELDS)
                    if slot < min(len(lap), len(tel), len(mo)):
                        b.add_pos(lap[slot], tel[slot], mo[slot])
                if len(pend[kind]) > WINDOW:
                    pend[kind].pop(next(iter(pend[kind])))
    return b.laps, length


def build(laps, length, lap_name, ref_name, seg_m=100):
    by_label = {lap_label(k): v for k, v in laps.items()}
    if lap_name not in by_label or ref_name not in by_label:
        raise SystemExit(f"Laps available: {', '.join(by_label)}")
    lap, ref = by_label[lap_name], by_label[ref_name]
    pts, last = [], -1e9
    for d, x, z in sorted(p for p in lap.get("pos", []) if p[1] is not None):
        if d - last >= 10:
            pts.append([round(d, 1), round(x, 2), round(z, 2)])
            last = d
    segs = [{"from_m": s["from_m"], "to_m": s["to_m"], "gain_ms": s["loss_ms"],
             "lap_min_kmh": (s["best"] or {}).get("min_speed"), "ref_min_kmh": (s["you"] or {}).get("min_speed")}
            for s in compare(ref, lap, length, seg_m)]   # compare(a, b): loss of a vs b = gain of b
    return {"lap": lap_name, "lap_ms": lap["time_ms"], "ref": ref_name, "ref_ms": ref["time_ms"],
            "track_length": length, "points": pts, "segments": segs}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--lap", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--segment-m", type=float, default=100.0)
    a = ap.parse_args(argv)
    laps, length = load(a.path)
    print(json.dumps(build(laps, length, a.lap, a.ref, a.segment_m), separators=(",", ":")))


if __name__ == "__main__":
    main()
