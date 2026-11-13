"""Fuel planner: how much fuel to start the race with, from your own laps at this race meeting.

  python -m race_engineer.fuel_plan SESSION_FOLDER [SESSION_FOLDER ...] --laps N [--margin-laps 1.0]
  python -m race_engineer.fuel_plan --track 0 --laps N          (every saved session at that track)

For each of your laps in the given sessions it measures fuel used = fuelInTank at the start of the
lap minus at the start of the next lap (CarStatus, joined to LapData by frameIdentifier). Laps are
kept only if they are a full racing lap: lap time present, no pit lane, not invalid, the fuel went
down, and no safety car (Session.safetyCarStatus not 0 at any point in the lap, if the game sends it).
Laps are grouped dry / wet by Session.weather at the start of the lap (0-2 dry, 3+ wet; the game's
weather codes, see tools.WEATHER_NAMES).

Plan = burn per lap (median) x (race laps + 1 formation lap) + margin laps x burn.
The formation lap and the margin are my assumptions and are shown as such; the burn is measured.
Read-only on the session data.
"""
import argparse
import json
import statistics
from pathlib import Path

KEYS = ('"packet_name": "Participants"', '"packet_name": "LapData"', '"packet_name": "CarStatus"',
        '"packet_name": "Session"')
WINDOW = 64
SESSIONS = Path(__file__).resolve().parent.parent.parent / "sessions"


def laps_from(path):
    """Return (track_id, session_type, total_laps, [lap dicts]) for your car in one parsed.jsonl."""
    slot = track = stype = total = None
    weather = sc = None
    pend = {"LapData": {}, "CarStatus": {}}
    laps, cur = [], None
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
            elif kind == "Session":
                track = d.get("trackId", track)
                stype = d.get("sessionType", stype)
                total = d.get("totalLaps", total)
                weather = d.get("weather", weather)
                sc = d.get("safetyCarStatus", sc)
                if cur is not None and sc:
                    cur["sc"] = True
            elif kind in pend and slot is not None:
                fr = d.get("frameIdentifier")
                pend[kind][fr] = d["lapData" if kind == "LapData" else "carStatusData"]
                if fr in pend["LapData"] and fr in pend["CarStatus"]:
                    ld, st = pend["LapData"].pop(fr), pend["CarStatus"].pop(fr)
                    if slot < len(ld) and slot < len(st):
                        ld, st = ld[slot], st[slot]
                        num, fuel = ld["currentLapNum"], st.get("fuelInTank")
                        if cur is None or num != cur["lap"]:
                            if cur is not None and num == cur["lap"] + 1:
                                cur["fuel_end"] = fuel
                                cur["time_ms"] = ld.get("lastLapTimeInMS") or None
                                laps.append(cur)
                            cur = {"lap": num, "fuel_start": fuel, "weather": weather, "pit": False,
                                   "invalid": False, "sc": bool(sc), "mix": st.get("fuelMix")}
                        cur["pit"] = cur["pit"] or bool(ld.get("pitStatus")) or bool(ld.get("pitLaneTimerActive"))
                        cur["invalid"] = cur["invalid"] or bool(ld.get("currentLapInvalid"))
                if len(pend[kind]) > WINDOW:
                    pend[kind].pop(next(iter(pend[kind])))
    return track, stype, total, laps


def usable(lap):
    used = (lap["fuel_start"] or 0) - (lap.get("fuel_end") or 0)
    return (lap.get("time_ms") and not lap["pit"] and not lap["invalid"] and not lap["sc"]
            and lap.get("fuel_end") is not None and used > 0)


def plan(folders, race_laps, margin_laps=1.0, formation_laps=1):
    rows, tracks = [], set()
    for folder in folders:
        p = Path(folder) / "parsed.jsonl"
        if not p.is_file():
            continue
        track, stype, total, laps = laps_from(p)
        tracks.add(track)
        for l in laps:
            if usable(l):
                rows.append({"session": Path(folder).name, "session_type": stype, "lap": l["lap"],
                             "kg": round(l["fuel_start"] - l["fuel_end"], 3),
                             "wet": (l["weather"] or 0) >= 3, "mix": l["mix"], "time_ms": l["time_ms"]})
    out = {"tracks": sorted(t for t in tracks if t is not None), "laps_used": rows, "race_laps": race_laps,
           "assumptions": {"formation_laps": formation_laps, "margin_laps": margin_laps}}
    for cond in ("dry", "wet"):
        v = [r["kg"] for r in rows if r["wet"] == (cond == "wet")]
        if not v:
            out[cond] = None
            continue
        burn = statistics.median(v)
        need = burn * (race_laps + formation_laps + margin_laps)
        out[cond] = {"laps": len(v), "burn_kg_per_lap": round(burn, 3),
                     "spread_kg": [round(min(v), 3), round(max(v), 3)], "start_fuel_kg": round(need, 1)}
    return out


def report(res):
    lines = [f"Fuel plan for a {res['race_laps']}-lap race (tracks in data: {res['tracks']})."]
    a = res["assumptions"]
    for cond in ("dry", "wet"):
        r = res[cond]
        if not r:
            lines.append(f"  {cond}: no usable laps of yours in these sessions.")
            continue
        lines.append(f"  {cond}: you burn {r['burn_kg_per_lap']} kg a lap (median of {r['laps']} laps, "
                     f"range {r['spread_kg'][0]}-{r['spread_kg'][1]}) -> start with {r['start_fuel_kg']} kg")
    lines.append(f"  Includes {a['formation_laps']} formation lap and {a['margin_laps']} lap of margin "
                 f"(my assumptions; change with --margin-laps). Burn is measured from your laps.")
    lines.append("  Laps used: " + ", ".join(f"{r['session'][:16]} lap {r['lap']} {r['kg']}kg"
                                             f"{' wet' if r['wet'] else ''}" for r in res["laps_used"][:30]))
    return "\n".join(lines)


def folders_for_track(track_id, sessions=SESSIONS, min_mb=50):
    out = []
    for f in sorted(Path(sessions).iterdir()):
        p = f / "parsed.jsonl"
        if not p.is_file() or p.stat().st_size < min_mb * 1e6:
            continue
        with open(p, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if '"packet_name": "Session"' in line[:120]:
                    if json.loads(line).get("trackId") == track_id:
                        out.append(f)
                    break
                if i > 20000:
                    break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="*")
    ap.add_argument("--track", type=int, default=None)
    ap.add_argument("--laps", type=int, required=True, help="race length in laps")
    ap.add_argument("--margin-laps", type=float, default=1.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    folders = list(a.folders) + ([str(f) for f in folders_for_track(a.track)] if a.track is not None else [])
    if not folders:
        raise SystemExit("Give session folders, or --track N.")
    res = plan(folders, a.laps, a.margin_laps)
    print(json.dumps(res, indent=1) if a.json else report(res))


if __name__ == "__main__":
    main()
