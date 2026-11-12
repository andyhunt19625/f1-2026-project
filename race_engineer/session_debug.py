"""Diagnostics for one car in a saved session, in recorded order:
- every change of lap number, pitStatus, driverStatus or validity (with lap time so far);
- every time the lap timer jumps back during a lap (a timer reset);
- every change in body damage;
- with --from/--to, a sample every --every seconds in that window.

  python -m race_engineer.session_debug PATH/parsed.jsonl [--driver NAME] [--from S --to S --every 5]

driverStatus per the F1 UDP spec: 0 in garage, 1 flying lap, 2 in lap, 3 out lap, 4 on track.
Read-only.
"""
import argparse
import unicodedata

from .session_reader import read_events
from .tools import DAMAGE_PARTS


def _norm(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def run(path, driver=None, t_from=None, t_to=None, every=5.0):
    slot = name = None
    last_key = last_dmg = None
    last_t_ms = None
    next_sample = t_from
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
            st = ev[1] or 0.0
            ld = ev[2][slot]
            desc = (f"lap {ld['currentLapNum']:>2} driverStatus={ld.get('driverStatus')} "
                    f"pitStatus={ld['pitStatus']} invalid={ld['currentLapInvalid']} "
                    f"dist={ld['lapDistance']:.0f} m lapTime={ld['currentLapTimeInMS']} ms "
                    f"lastLap={ld['lastLapTimeInMS']} ms")
            key = (ld["currentLapNum"], ld["pitStatus"], ld["currentLapInvalid"], ld.get("driverStatus"))
            if key != last_key:
                lines.append(f"t={st:8.1f}s CHANGE  {desc}")
                last_key = key
            elif last_t_ms is not None and ld["currentLapTimeInMS"] + 2000 < last_t_ms:
                lines.append(f"t={st:8.1f}s TIMER RESET (was {last_t_ms} ms)  {desc}")
            last_t_ms = ld["currentLapTimeInMS"]
            if t_from is not None and t_from <= st <= (t_to if t_to is not None else st) and st >= next_sample:
                lines.append(f"t={st:8.1f}s sample  {desc}")
                next_sample = st + every
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
    ap.add_argument("--from", dest="t_from", type=float, default=None)
    ap.add_argument("--to", dest="t_to", type=float, default=None)
    ap.add_argument("--every", type=float, default=5.0)
    a = ap.parse_args(argv)
    name, lines = run(a.path, a.driver, a.t_from, a.t_to, a.every)
    print(f"Driver: {name}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
