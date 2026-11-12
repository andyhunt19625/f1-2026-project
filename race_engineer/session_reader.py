"""Shared reader for saved sessions (parsed.jsonl), used by the lap tools.

Two things it gets right that a naive reader would not:
- Events are yielded in FILE ORDER (the order capture recorded them), never sorted by
  frameIdentifier, so a frame counter that starts again (new run, restart) cannot mix
  or overwrite data.
- LapData and CarTelemetry are paired by frameIdentifier as they arrive, keeping only a
  small window of unmatched frames.
"""
import json

KEYS = ('"packet_name": "Participants"', '"packet_name": "LapData"',
        '"packet_name": "CarTelemetry"', '"packet_name": "Session"',
        '"packet_name": "CarSetups"', '"packet_name": "CarDamage"')
WINDOW = 64


def read_events(path):
    """Yield ('participants', d) | ('session', d) | ('setups', d) | ('damage', d) |
    ('frame', sessionTime, lapData_list, telemetry_list) in file order."""
    pend_lap, pend_tel = {}, {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            head = line[:120]
            if not any(k in head for k in KEYS):
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
            elif kind == "CarSetups":
                yield "setups", d
            elif kind == "CarDamage":
                yield "damage", d
            elif kind in ("LapData", "CarTelemetry"):
                fr = d.get("frameIdentifier")
                mine, other = (pend_lap, pend_tel) if kind == "LapData" else (pend_tel, pend_lap)
                payload = d["lapData"] if kind == "LapData" else d["carTelemetryData"]
                if fr in other:
                    match = other.pop(fr)
                    lap, tel = (payload, match) if kind == "LapData" else (match, payload)
                    yield "frame", d.get("sessionTime"), lap, tel
                else:
                    mine[fr] = payload
                    if len(mine) > WINDOW:
                        mine.pop(next(iter(mine)))


class LapBuilder:
    """Builds one car's laps. A lap is keyed (run, lapNum); a new run starts whenever the
    lap number goes backwards, so laps from different runs are never merged."""

    def __init__(self):
        self.laps = {}
        self.prev_num = None
        self.prev_key = None
        self.run = 1

    def add(self, ld, tel):
        num = ld["currentLapNum"]
        if self.prev_num is not None and num < self.prev_num:
            self.run += 1
        key = (self.run, num)
        lap = self.laps.setdefault(key, {"samples": [], "time_ms": None, "valid": True, "pit": False})
        if (self.prev_key is not None and key != self.prev_key and key[0] == self.prev_key[0]
                and num == self.prev_num + 1 and self.prev_key in self.laps):
            self.laps[self.prev_key]["time_ms"] = ld["lastLapTimeInMS"] or None
        self.prev_num, self.prev_key = num, key
        lap["valid"] = lap["valid"] and not ld["currentLapInvalid"]
        lap["pit"] = lap["pit"] or bool(ld["pitStatus"])
        if ld["lapDistance"] >= 0:
            lap["samples"].append((ld["lapDistance"], ld["currentLapTimeInMS"], tel["speed"],
                                   tel["throttle"], tel["brake"], tel["gear"], tel.get("steer", 0.0)))


def lap_label(key):
    run, num = key
    return f"lap {num}" if run == 1 else f"run {run} lap {num}"
