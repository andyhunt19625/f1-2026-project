"""Question tools. Each returns a dict with a short 'answer' for speech, the
numbers behind it, and 'unverified' notes for anything not yet checked.
Claude (or the typed CLI) only ever sees what these return."""
import unicodedata

from .state import ms_from_parts

DEGRADE_THRESHOLD_PCT = 30.0   # Andy: degraded as soon as wear hits 30%
DEFAULT_BUFFER_S = 5.0         # Andy: buffer between 2 and 5 seconds
BUFFER_RANGE_S = (2.0, 5.0)
RATE_WINDOW_LAPS = 3           # laps of history used for the wear rate

# Spec order for tyresWear per the F1 25 UDP spec: RL, RR, FL, FR. NOT yet confirmed
# against the game (brief: "still unverified"), so every tyre answer carries a note.
WHEELS = ("rear-left", "rear-right", "front-left", "front-right")


class ToolError(Exception):
    pass


def _norm(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def find_driver(state, query):
    """Look a driver up by name from the Participants packet (never by slot)."""
    q = _norm(query).strip()
    if q in ("me", "i", "myself", ""):
        slot = state.player_slot()
        if slot is None:
            raise ToolError("Cannot tell which car is yours yet.")
        return slot
    tokens = set(q.replace("-", " ").split())
    hits = []
    for i, p in enumerate(state.participants[: state._n()]):
        n = _norm(p["name"])
        if n in tokens or n == q or str(p.get("raceNumber")) in tokens:
            hits.append(i)
    if not hits:
        raise ToolError(f"No driver matching '{query}'.")
    if len(hits) > 1:
        raise ToolError(f"'{query}' matches more than one driver.")
    return hits[0]


def _need_ready(state):
    if not state.ready():
        raise ToolError("No race data yet.")


def _leader_ms(ld):
    return ms_from_parts(ld["deltaToRaceLeaderMSPart"], ld["deltaToRaceLeaderMinutesPart"])


def gap_to(state, driver):
    with state.lock:
        _need_ready(state)
        me = state.player_slot()
        if me is None:
            raise ToolError("Cannot tell which car is yours yet.")
        other = find_driver(state, driver)
        if other == me:
            raise ToolError("That is your own car.")
        a, b = state.lap_data[me], state.lap_data[other]
        diff_ms = _leader_ms(b) - _leader_ms(a)  # positive: other is behind me
        secs = abs(diff_ms) / 1000.0
        ahead = a["carPosition"] < b["carPosition"]
        name = state.name_of(other).title()
        notes = ["Gap = difference of the two deltaToRaceLeader values; not yet checked "
                 "against the game's on-screen gap."]
        if a["currentLapNum"] != b["currentLapNum"]:
            notes.append("Cars are on different laps; the gap may not be meaningful.")
        text = f"{name} is {secs:.1f} seconds {'behind' if ahead else 'ahead of'} you."
        return {"answer": text, "gap_s": round(secs, 3), "other_is_behind": ahead,
                "other_position": b["carPosition"], "your_position": a["carPosition"],
                "unverified": notes}


def tyre_status(state, driver="me"):
    with state.lock:
        _need_ready(state)
        slot = find_driver(state, driver)
        if slot >= len(state.car_damage):
            raise ToolError("No tyre data yet.")
        wear = list(state.car_damage[slot]["tyresWear"])
        worst = max(wear)
        who = "Your" if slot == state.player_slot() else state.name_of(slot).title() + "'s"
        hist = state.wear_history.get(slot, [])
        # rate of the most worn wheel over the last few completed laps of this stint
        pts = [(h["laps_completed"], max(h["wear"])) for h in hist][-(RATE_WINDOW_LAPS + 1):]
        rate = None
        if len(pts) >= 2 and pts[-1][0] > pts[0][0]:
            rate = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])
        parts = [f"{who} most worn tyre is at {worst:.0f} percent."]
        laps_left = None
        if worst >= DEGRADE_THRESHOLD_PCT:
            parts.append(f"That is past the {DEGRADE_THRESHOLD_PCT:.0f} percent degrade point.")
            laps_left = 0.0
        elif rate and rate > 0:
            laps_left = (DEGRADE_THRESHOLD_PCT - worst) / rate
            parts.append(f"At about {rate:.1f} percent a lap, it reaches "
                         f"{DEGRADE_THRESHOLD_PCT:.0f} percent in roughly {laps_left:.0f} laps.")
        else:
            parts.append("Not enough laps on these tyres yet to estimate a wear rate.")
        return {"answer": " ".join(parts),
                "wear_pct": dict(zip(WHEELS, wear)), "worst_pct": worst,
                "rate_pct_per_lap": rate, "laps_to_degrade": laps_left,
                "threshold_pct": DEGRADE_THRESHOLD_PCT,
                "unverified": ["Wheel order (RL, RR, FL, FR) is from the spec, not yet confirmed "
                               "in the game. 'Most worn' does not depend on it."]}


def pit_window(state, pit_loss_s, buffer_s=DEFAULT_BUFFER_S):
    """Come out ahead of the car behind if gap_behind - pit_loss >= buffer."""
    lo, hi = BUFFER_RANGE_S
    if not lo <= buffer_s <= hi:
        raise ToolError(f"Buffer must be between {lo:g} and {hi:g} seconds.")
    if pit_loss_s is None:
        raise ToolError("Pit loss for this circuit has not been measured yet.")
    with state.lock:
        _need_ready(state)
        me = state.player_slot()
        if me is None:
            raise ToolError("Cannot tell which car is yours yet.")
        mine = state.lap_data[me]
        behind = [i for i in range(state._n()) if state.lap_data[i]["carPosition"] == mine["carPosition"] + 1]
        if not behind:
            raise ToolError("Nobody is behind you.")
        b = behind[0]
        gap = ms_from_parts(state.lap_data[b]["deltaToCarInFrontMSPart"],
                            state.lap_data[b]["deltaToCarInFrontMinutesPart"]) / 1000.0
        clear = gap - pit_loss_s
        need = pit_loss_s + buffer_s
        ok = clear >= buffer_s
        name = state.name_of(b).title()
        if ok:
            text = (f"Yes. {name} is {gap:.1f} seconds behind; you would rejoin {clear:.1f} "
                    f"seconds clear.")
        elif clear < 0:
            text = (f"No. {name} is {gap:.1f} seconds behind; you would rejoin "
                    f"{-clear:.1f} seconds behind them. You need a {need:.1f} second gap.")
        else:
            text = (f"No. {name} is {gap:.1f} seconds behind; you would rejoin "
                    f"{clear:.1f} seconds clear, short of {buffer_s:g}. You need a "
                    f"{need:.1f} second gap.")
        return {"answer": text, "in_window": ok, "car_behind": name, "gap_behind_s": round(gap, 3),
                "pit_loss_s": pit_loss_s, "rejoin_clear_s": round(clear, 3),
                "buffer_s": buffer_s, "gap_needed_s": round(need, 3),
                "unverified": ["Pit loss is whatever figure was passed in; the per-circuit "
                               "measurement is still to be built."]}


# Weather codes per the F1 UDP spec. Only code 0 has been seen in a saved session, so the
# other names are unconfirmed until the wet test.
WEATHER_NAMES = {0: "clear", 1: "light cloud", 2: "overcast", 3: "light rain",
                 4: "heavy rain", 5: "storm"}


def _wname(code):
    return WEATHER_NAMES.get(code, f"weather code {code}")


def weather_change(state):
    """First forecast sample whose weather differs from the current weather."""
    with state.lock:
        s = state.session
        if not s or not s.get("weatherForecastSamples"):
            raise ToolError("No weather forecast yet.")
        cur = s["weather"]
        # The Session packet can carry forecasts for other session types too (seen on
        # 2026-11-12: this session type 15, extra samples tagged type 16). Use only this
        # session's own samples.
        st = s.get("sessionType")
        samples = [x for x in s["weatherForecastSamples"] if st is None or x.get("sessionType", st) == st]
        if not samples:
            raise ToolError("No weather forecast for this session yet.")
        peak = max(samples, key=lambda x: x["rainPercentage"])
        change = next((x for x in samples if x["weather"] != cur), None)
        notes = ["timeOffset is assumed to be minutes from now (spec), not yet confirmed.",
                 "Weather names other than 'clear' are from the spec, not yet seen in a saved session."]
        if change:
            mins = change["timeOffset"]
            text = (f"The weather is forecast to change from {_wname(cur)} to "
                    f"{_wname(change['weather'])} in about {mins} minutes, with a "
                    f"{change['rainPercentage']} percent chance of rain.")
        else:
            horizon = samples[-1]["timeOffset"]
            text = (f"No change forecast over the next {horizon} minutes. Rain chance peaks at "
                    f"{peak['rainPercentage']} percent.")
        return {"answer": text, "current_weather": cur, "current_name": _wname(cur),
                "change_at_minutes": change["timeOffset"] if change else None,
                "change_to": change["weather"] if change else None,
                "peak_rain_pct": peak["rainPercentage"], "peak_rain_at_minutes": peak["timeOffset"],
                "forecast": [{k: x[k] for k in ("timeOffset", "weather", "rainPercentage")}
                             for x in samples],
                "unverified": notes}


TOOLS = {"gap_to": gap_to, "tyre_status": tyre_status, "pit_window": pit_window,
         "weather_change": weather_change}
