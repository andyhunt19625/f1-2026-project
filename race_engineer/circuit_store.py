"""Per-circuit memory of what has been measured in real sessions, kept between races:
pit loss from real pit stops and the time spent in the pit lane.

File: run/circuit_data.json in the engineer folder (on the PC: D:\\RealF1-esports\\engineer\\run).
  { "<trackId>": { "pit_losses":  [{"when", "session_type", "driver", "loss_s", "ref_s", "laps"}],
                   "pit_lane_ms": [{"when", "session_type", "driver", "ms"}] } }
Only stops where the game's numPitStops went up are stored (not garage visits).
Written straight away after each stop (write to a temp file, then replace), so nothing is lost
if the engineer stops mid-race.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "run" / "circuit_data.json"
RACE_TYPES = (15, 16, 17)     # Session.sessionType race values (F1 25 spec)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def add(path, track_id, kind, row):
    if path is None or track_id is None:
        return
    data = load(path)
    row = dict(row, when=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    data.setdefault(str(track_id), {}).setdefault(kind, []).append(row)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def stored_pit_loss(path, track_id):
    """Median pit loss from earlier RACES at this track, and how many stops it is based on."""
    if path is None or track_id is None:
        return None, 0
    rows = [r["loss_s"] for r in load(path).get(str(track_id), {}).get("pit_losses", [])
            if r.get("session_type") in RACE_TYPES]
    if not rows:
        return None, 0
    rows.sort()
    return rows[len(rows) // 2], len(rows)
