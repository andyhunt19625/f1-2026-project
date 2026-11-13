"""Race events the game sends (Event packets and Session.safetyCarStatus), decoded into plain words.

Field names were read from Andy's Australia race recording (13 Nov 2026):
  Event: eventCode, eventName, details.  Session: safetyCarStatus, numSafetyCarPeriods,
  numVirtualSafetyCarPeriods, numRedFlagPeriods.
Number meanings below are from the F1 25 UDP specification. Checked against the race:
  safetyCarStatus 3 during the formation lap, 1 from lap 5 to lap 8 (the safety car Andy saw);
  penaltyType 5 for Andy's two warnings (Andy confirmed they were warnings, not penalties);
  RTMT for Hulkenberg on lap 13.
Only some numbers have been seen in real data so far: safetyCarStatus 0/1/3, SCAR eventType 0-3,
penaltyType 5 and 16, infringementType 4 (Andy's contact with Piastri), 7 and 41. The rest of the
tables are the specification's names and are marked "(spec)" in answers until seen.
Anything not in these tables is reported by its number, never guessed.
"""

SAFETY_CAR = {0: "no safety car", 1: "full safety car", 2: "virtual safety car", 3: "formation lap"}
SC_EVENT = {0: "deployed", 1: "returning to the pits", 2: "returned to the pits", 3: "race resumed"}

PENALTY_TYPES = {
    0: "drive through", 1: "stop go", 2: "grid penalty", 3: "penalty reminder", 4: "time penalty",
    5: "warning", 6: "disqualified", 7: "removed from formation lap", 8: "parked too long timer",
    9: "tyre regulations", 10: "this lap invalidated", 11: "this and next lap invalidated",
    12: "this lap invalidated without reason", 13: "this and next lap invalidated without reason",
    14: "this and previous lap invalidated", 15: "this and previous lap invalidated without reason",
    16: "retired", 17: "black flag timer",
}
INFRINGEMENTS = {
    0: "blocking by slow driving", 1: "blocking by wrong way driving", 2: "reversing off the start line",
    3: "big collision", 4: "small collision", 5: "collision, failed to hand back position (single)",
    6: "collision, failed to hand back position (multiple)", 7: "corner cutting, gained time",
    8: "corner cutting overtake (single)", 9: "corner cutting overtake (multiple)",
    10: "crossed pit exit lane", 11: "ignoring blue flags", 12: "ignoring yellow flags",
    13: "ignoring drive through", 14: "too many drive throughs", 15: "drive through reminder serve within n laps",
    16: "drive through penalty reminder serve this lap", 17: "pit lane speeding", 18: "parked for too long",
    19: "ignoring tyre regulations", 20: "too many penalties", 21: "multiple warnings",
    22: "approaching disqualification", 23: "tyre regulations select single", 24: "tyre regulations select multiple",
    25: "lap invalidated, corner cutting", 26: "lap invalidated, running wide", 27: "corner cutting ran wide, gained time minor",
    28: "corner cutting ran wide, gained time significant", 29: "corner cutting ran wide, gained time extreme",
    30: "lap invalidated, wall riding", 31: "lap invalidated, flashback used", 32: "lap invalidated, reset to track",
    33: "blocking the pitlane", 34: "jump start", 35: "safety car to car collision", 36: "safety car illegal overtake",
    37: "safety car exceeding allowed pace", 38: "virtual safety car exceeding allowed pace",
    39: "formation lap below allowed speed", 40: "formation lap parking", 41: "retired mechanical failure",
    42: "retired terminally damaged", 43: "safety car falling too far back", 44: "black flag timer",
    45: "unserved stop go penalty", 46: "unserved drive through penalty", 47: "engine component change",
    48: "gearbox change", 49: "parc ferme change", 50: "league grid penalty", 51: "retry penalty",
    52: "illegal time gain", 53: "mandatory pitstop", 54: "attribute assigned",
}
# Codes kept in the race log. BUTN (button presses), SPTP (speed traps) and OVTK (overtakes) are
# very frequent (3650, 461 and 318 in the race) and are counted instead of listed.
LISTED = ("SCAR", "PENA", "RTMT", "COLL", "FLBK", "FTLP", "RCWN", "CHQF", "SSTA", "SEND", "DRSE", "DRSD",
          "RDFL", "TMPT", "LGOT")
COUNTED = ("BUTN", "SPTP", "OVTK")


def name(names, idx):
    if idx is None or idx == 255:
        return None
    return names[idx].title() if 0 <= idx < len(names) else f"car {idx}"


SEEN_INFRINGEMENTS = {4, 7, 41}
SEEN_PENALTIES = {5, 16}


def describe(code, details, names, me=None):
    """One plain sentence for an event, or None for codes that are only counted."""
    d = details or {}
    who = lambda k: name(names, d.get(k))
    you = lambda k: d.get(k) == me
    if code == "SCAR":
        return (f"{SAFETY_CAR.get(d.get('safetyCarType'), 'safety car type ' + str(d.get('safetyCarType'))).capitalize()} "
                f"{SC_EVENT.get(d.get('eventType'), 'event ' + str(d.get('eventType')))}.")
    if code == "PENA":
        pt, it = d.get("penaltyType"), d.get("infringementType")
        p = PENALTY_TYPES.get(pt, f"penalty type {pt}") + ("" if pt in SEEN_PENALTIES else " (spec)")
        i = INFRINGEMENTS.get(it, f"infringement {it}") + ("" if it in SEEN_INFRINGEMENTS else " (spec)")
        target = "You" if you("vehicleIdx") else who("vehicleIdx")
        other = who("otherVehicleIdx")
        return f"{target}: {p} for {i}" + (f" (with {other})" if other else "") + f", lap {d.get('lapNum')}."
    if code == "RTMT":
        return f"{'You' if you('vehicleIdx') else who('vehicleIdx')} retired (reason {d.get('reason')})."
    if code == "COLL":
        a, b = d.get("vehicle1Idx"), d.get("vehicle2Idx")
        if me is not None and me not in (a, b):
            return None              # only your own contact is listed; others are counted
        other = b if a == me else a
        return f"Contact with {name(names, other)} (severity {d.get('severity')})."
    if code == "FLBK":
        return f"Flashback used, back to session time {d.get('flashbackSessionTime', 0):.1f} s."
    if code == "FTLP":
        return f"Fastest lap: {'you' if you('vehicleIdx') else who('vehicleIdx')} {d.get('lapTime', 0):.3f} s."
    if code == "RCWN":
        return f"Race winner: {'you' if you('vehicleIdx') else who('vehicleIdx')}."
    if code == "CHQF":
        return "Chequered flag."
    if code == "SSTA":
        return "Session started."
    if code == "SEND":
        return "Session ended."
    if code == "LGOT":
        return "Lights out."
    if code in ("DRSE", "DRSD"):
        return "DRS enabled." if code == "DRSE" else "DRS disabled."
    if code == "RDFL":
        return "Red flag."
    return None
