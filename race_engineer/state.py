"""Live state layer: keeps the latest numbers in memory for the question tools.

Input is one decoded packet at a time, as a flat dict in the same shape as a
line of parsed.jsonl: {"packet_name": ..., "sessionTime": ..., <body fields>}.
Field names below were read from the saved Jeddah race (session
20261029T112645Z_af19db5fa05de5b6), not guessed.

Everything here is read-only with respect to the capture service.
"""
import threading
from collections import Counter

from .live_laps import LiveLaps

WANTED = ("Session", "Participants", "LapData", "CarDamage", "CarStatus", "CarTelemetry")


def ms_from_parts(ms_part, minutes_part):
    """LapData gaps are split into a milliseconds part and a minutes part."""
    return int(minutes_part) * 60000 + int(ms_part)


class LiveState:
    def __init__(self, player_slot=None, lap_log=None):
        self.lock = threading.RLock()
        self.player_slot_override = player_slot
        self.lap_log = lap_log
        self._clear()

    def reset(self):
        """Forget everything (a new game session started)."""
        with self.lock:
            self._clear()

    def _clear(self):
        self.session = None
        self.participants = []
        self.num_active = 0
        self.lap_data = []
        self.car_damage = []
        self.car_status = []
        self.session_time = None
        self.counts = Counter()
        # Per car, tyre wear sampled once per lap change within the current stint:
        # list of {"laps_completed": int, "wear": [4 floats]}
        self.wear_history = {}
        self._last_lap = {}
        self._last_stops = {}
        # Per car pit lane time in lane: {"active": bool, "current_ms": int, "completed_ms": [..]}
        self.pit_lane = {}
        # Lap-by-lap analysis while driving (see live_laps.py)
        self.live_laps = LiveLaps(self.lap_log) if self.lap_log else LiveLaps()

    # ---- ingest -------------------------------------------------------
    def ingest(self, pkt):
        name = pkt.get("packet_name")
        if name not in WANTED:
            return
        with self.lock:
            self.counts[name] += 1
            try:
                self.live_laps.ingest(pkt)
            except Exception:          # the lap analysis must never stop the question tools
                self.counts["live_laps_errors"] += 1
            if "sessionTime" in pkt:
                self.session_time = pkt["sessionTime"]
            if name == "Session":
                self.session = pkt
            elif name == "Participants":
                self.participants = pkt["participants"]
                self.num_active = pkt.get("numActiveCars", len(self.participants))
            elif name == "CarDamage":
                self.car_damage = pkt["carDamageData"]
            elif name == "CarStatus":
                self.car_status = pkt["carStatusData"]
            elif name == "LapData":
                self.lap_data = pkt["lapData"]
                self._track(pkt["lapData"])

    def _n(self):
        return self.num_active or len(self.participants)

    def _track(self, laps):
        for i in range(min(self._n(), len(laps))):
            ld = laps[i]
            # new stint after a pit stop: drop the old tyres' history
            stops = ld["numPitStops"]
            if stops > self._last_stops.get(i, stops):
                self.wear_history[i] = []
            self._last_stops[i] = stops
            lap = ld["currentLapNum"]
            if lap != self._last_lap.get(i) and i < len(self.car_damage):
                self.wear_history.setdefault(i, []).append(
                    {"laps_completed": lap - 1, "wear": list(self.car_damage[i]["tyresWear"])})
            self._last_lap[i] = lap
            pl = self.pit_lane.setdefault(i, {"active": False, "current_ms": 0, "completed_ms": []})
            if ld["pitLaneTimerActive"]:
                pl["active"] = True
                pl["current_ms"] = ld["pitLaneTimeInLaneInMS"]
            elif pl["active"]:
                pl["active"] = False
                if pl["current_ms"] > 0:
                    pl["completed_ms"].append(pl["current_ms"])
                pl["current_ms"] = 0

    # ---- lookups ------------------------------------------------------
    def player_slot(self):
        """Andy's slot: first participant with aiControlled == 0 (read at run time)."""
        if self.player_slot_override is not None:
            return self.player_slot_override
        for i, p in enumerate(self.participants[: self._n()]):
            if p.get("aiControlled") == 0:
                return i
        return None

    def name_of(self, slot):
        return self.participants[slot]["name"]

    def ready(self):
        return bool(self.participants and self.lap_data)
