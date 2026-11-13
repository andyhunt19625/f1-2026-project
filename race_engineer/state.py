"""Live state layer: keeps the latest numbers in memory for the question tools.

Input is one decoded packet at a time, as a flat dict in the same shape as a
line of parsed.jsonl: {"packet_name": ..., "sessionTime": ..., <body fields>}.
Field names below were read from the saved Jeddah race (session
20261029T112645Z_af19db5fa05de5b6), not guessed.

Everything here is read-only with respect to the capture service.
"""
import threading
from collections import Counter

from . import circuit_store, events as ev
from .live_laps import LiveLaps

WANTED = ("Session", "Participants", "LapData", "CarDamage", "CarStatus", "CarTelemetry", "Event")


def ms_from_parts(ms_part, minutes_part):
    """LapData gaps are split into a milliseconds part and a minutes part."""
    return int(minutes_part) * 60000 + int(ms_part)


class LiveState:
    def __init__(self, player_slot=None, lap_log=None, circuit_store_path=None):
        self.lock = threading.RLock()
        self.player_slot_override = player_slot
        self.lap_log = lap_log
        self.circuit_store_path = circuit_store_path   # None = do not store (tests, simulator choose)
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
        # Pit loss measured from real stops this session (see _pit_loss_check)
        self._laps = {}         # car -> list of {"lap", "ms", "pit", "stops_end"}
        self._lap_pit = {}      # car -> pit lane seen during the current lap
        self.pit_losses = []    # {"driver", "laps", "loss_s", "ref_s"}
        # Race events (Event packets + safety car status), see events.py
        self.events = []        # {"session_time", "lap", "code", "text", "details"}
        self.event_counts = Counter()
        self.safety_car = None  # last Session.safetyCarStatus
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
                sc = pkt.get("safetyCarStatus")
                if sc is not None and sc != self.safety_car:
                    if self.safety_car is not None:
                        self._event("SC_STATUS", {"from": self.safety_car, "to": sc},
                                    f"Safety car status: {ev.SAFETY_CAR.get(sc, sc)}.")
                    self.safety_car = sc
            elif name == "Event":
                code = pkt.get("eventCode")
                self.event_counts[code] += 1
                if code in ev.LISTED:
                    text = ev.describe(code, pkt.get("details"), [p["name"] for p in self.participants],
                                       self.player_slot())
                    if text:
                        self._event(code, pkt.get("details"), text)
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
            if ld.get("pitStatus") or ld.get("pitLaneTimerActive"):
                self._lap_pit[i] = True
            prev = self._last_lap.get(i)
            if lap != prev and i < len(self.car_damage):
                self.wear_history.setdefault(i, []).append(
                    {"laps_completed": lap - 1, "wear": list(self.car_damage[i]["tyresWear"])})
            if prev is not None and lap == prev + 1 and ld.get("lastLapTimeInMS"):
                self._laps.setdefault(i, []).append({"lap": prev, "ms": ld["lastLapTimeInMS"],
                                                     "pit": self._lap_pit.get(i, False), "stops_end": stops})
                self._lap_pit[i] = bool(ld.get("pitStatus") or ld.get("pitLaneTimerActive"))
                self._pit_loss_check(i)
            self._last_lap[i] = lap
            pl = self.pit_lane.setdefault(i, {"active": False, "current_ms": 0, "completed_ms": []})
            if ld["pitLaneTimerActive"]:
                if not pl["active"]:
                    pl["stops_start"] = stops
                pl["active"] = True
                pl["current_ms"] = ld["pitLaneTimeInLaneInMS"]
            elif pl["active"]:
                pl["active"] = False
                if pl["current_ms"] > 0:
                    pl["completed_ms"].append(pl["current_ms"])
                    if stops > pl.get("stops_start", stops):     # a real stop, not a garage visit
                        self._store("pit_lane_ms", {"driver": self._name(i), "ms": pl["current_ms"]})
                pl["current_ms"] = 0

    def _pit_loss_check(self, i):
        """After a clean lap that follows a real pit stop (pit lane seen on the in-lap and/or
        out-lap AND numPitStops went up): loss = pit laps' time - (number of pit laps x this
        car's median of up to 3 clean laps before the stop, lap 1 excluded)."""
        laps = self._laps[i]
        if len(laps) < 3 or laps[-1]["pit"] or not laps[-2]["pit"]:
            return
        k = len(laps) - 2
        pit = []
        while k >= 0 and laps[k]["pit"]:
            pit.insert(0, laps[k])
            k -= 1
        before = [l for l in laps[: k + 1] if not l["pit"] and l["lap"] > 1][-3:]
        stops_before = laps[k]["stops_end"] if k >= 0 else 0
        if len(before) < 2 or pit[-1]["stops_end"] <= stops_before or len(pit) > 2:
            return
        ref = sorted(l["ms"] for l in before)[len(before) // 2] / 1000.0
        loss = sum(l["ms"] for l in pit) / 1000.0 - len(pit) * ref
        if 5.0 <= loss <= 60.0:     # outside this it is not a normal stop (my sanity limits)
            row = {"driver": self._name(i), "laps": [l["lap"] for l in pit],
                   "loss_s": round(loss, 3), "ref_s": round(ref, 3)}
            self.pit_losses.append(row)
            self._store("pit_losses", row)

    def _event(self, code, details, text):
        me = self.player_slot()
        lap = self.lap_data[me]["currentLapNum"] if me is not None and me < len(self.lap_data) else None
        self.events.append({"session_time": self.session_time, "lap": lap, "code": code,
                            "text": text, "details": details})

    def _name(self, i):
        return self.participants[i]["name"] if i < len(self.participants) else f"car {i}"

    def track_id(self):
        return (self.session or {}).get("trackId")

    def _store(self, kind, row):
        try:
            circuit_store.add(self.circuit_store_path, self.track_id(), kind,
                              dict(row, session_type=(self.session or {}).get("sessionType")))
        except OSError:
            self.counts["circuit_store_errors"] += 1

    def measured_pit_loss(self):
        """Median of pit losses measured from real stops this session, or None."""
        v = sorted(x["loss_s"] for x in self.pit_losses)
        return v[len(v) // 2] if v else None

    def stored_pit_loss(self):
        """(median, count) from earlier races at this track, from the circuit store."""
        return circuit_store.stored_pit_loss(self.circuit_store_path, self.track_id())

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
