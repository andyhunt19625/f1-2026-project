"""Diagnostics for one car in a saved session: every lap-number change (with pit status,
lap time and validity) and every change in body damage, in recorded order.

  python3 -m race_engineer.session_debug PATH/parsed.jsonl [--driver NAME]

Read-only. Useful for checking runs, lap numbering and damage against what happened.
"""
import argparse
import unicodedata

from .session_reader import read_events
from .tools import DAMAGE_PARTS


def _norm(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def run(path, driver=None):
    slot = name = None
    last_lap = None
    last_dmg = None
    lines = []
    st = None
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
        elif kind == "frame" and slot is not None:
            st = ev[1]
            ld = ev[2][slot]
            key = (ld["currentLapNum"], ld["pitStatus"], ld["currentLapInvalid"])
            if key != last_lap:
                lines.append(f"t={st:8.1f}s LAP {ld['currentLapNum']:>2} pitStatus={ld['pitStatus']} "
                             f"invalid={ld['currentLapInvalid']} lastLap={ld['lastLapTimeInMS']} ms "
                             f"dist={ld['lapDistance']:.0f} m")
                last_lap = key
        elif kind == "damage" and slot is not None:
            d = ev[1]["carDamageData"][slot]
            dmg = tuple(d.get(k, 0) for k, _ in DAMAGE_PARTS[:6])
            if dmg != last_dmg:
                parts = ", ".join(f"{label} {v}%" for (_, label), v in zip(DAMAGE_PARTS[:6], dmg) if v) or "none"
                lines.append(f"t={st if st is not None else 0:8.1f}s DAMAGE {parts}")
                last_dmg = dmg
    return name, lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--driver", default=None)
    a = ap.parse_args(argv)
    name, lines = run(a.path, a.driver)
    print(f"Driver: {name}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
