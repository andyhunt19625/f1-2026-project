"""Typed question console against a replayed session.

  python3 -m race_engineer.cli PATH/parsed.jsonl [--until SECONDS] [--speed 1] [--pit-loss S] [--buffer S]

--until replays instantly up to that session time, then you type questions.
--speed N replays at N x original speed in the background while you type.
"""
import argparse
import json
import threading

from .replay import replay
from .state import LiveState
from .tools import DEFAULT_BUFFER_S, ToolError, gap_to, pit_window, tyre_status

HELP = "gap <driver> | tyres [driver] | pit | who | quit"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--until", type=float, default=None)
    ap.add_argument("--speed", type=float, default=0.0)
    ap.add_argument("--pit-loss", type=float, default=None, help="seconds; not measured yet")
    ap.add_argument("--buffer", type=float, default=DEFAULT_BUFFER_S)
    a = ap.parse_args(argv)

    state = LiveState()
    if a.speed > 0:
        threading.Thread(target=replay, args=(a.path, state, a.speed, a.until), daemon=True).start()
    else:
        replay(a.path, state, 0.0, a.until)
        print(f"Replayed to session time {state.session_time}.")
    print(HELP)
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            continue
        cmd, _, arg = line.partition(" ")
        try:
            if cmd == "quit":
                break
            elif cmd == "gap":
                r = gap_to(state, arg)
            elif cmd == "tyres":
                r = tyre_status(state, arg or "me")
            elif cmd == "pit":
                r = pit_window(state, a.pit_loss, a.buffer)
            elif cmd == "who":
                s = state.player_slot()
                print(f"Your slot: {s}, name: {state.name_of(s) if s is not None else None}")
                continue
            else:
                print(HELP)
                continue
            print(r["answer"])
            print(json.dumps({k: v for k, v in r.items() if k != "answer"}, indent=1))
        except ToolError as e:
            print("Error:", e)


if __name__ == "__main__":
    main()
