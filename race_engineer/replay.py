"""Replay a saved session's parsed.jsonl into a LiveState, in time order.
Read-only: opens the file for reading and never touches the capture service."""
import json
import time

from .state import WANTED


def iter_packets(path):
    """Yield decoded packets of the types the state layer uses. Other lines are
    skipped without JSON decoding (the name sits near the start of each line)."""
    keys = tuple(f'"packet_name": "{n}"' for n in WANTED)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not any(k in line[:120] for k in keys):
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def replay(path, state, speed=0.0, until_session_time=None, stop=None):
    """speed 1.0 = original speed (by wall-clock 't'), 0 = as fast as possible.
    Stops after until_session_time (seconds) or when stop() returns True."""
    t0 = wall0 = None
    for pkt in iter_packets(path):
        if stop and stop():
            break
        if until_session_time is not None and pkt.get("sessionTime", 0) > until_session_time:
            break
        if speed > 0:
            t = pkt["t"]
            if t0 is None:
                t0, wall0 = t, time.monotonic()
            delay = (t - t0) / speed - (time.monotonic() - wall0)
            if delay > 0:
                time.sleep(delay)
        state.ingest(pkt)
    return state
