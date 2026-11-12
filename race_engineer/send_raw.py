"""Test tool: replay a saved session's raw.bin as UDP datagrams to the live
feed port, so the live path can be tested without the game or the capture
service. Read-only on the file. raw.bin frames are '<dI' (float64 wall-clock
time, uint32 length) followed by the packet bytes, as written by udp_capture.py.

  python3 -m race_engineer.send_raw PATH/raw.bin [--speed 1] [--until-seconds N]
"""
import argparse
import socket
import struct
import time

from .live import FORWARD_HOST, FORWARD_PORT

FRAME = struct.Struct("<dI")


def frames(path):
    with open(path, "rb") as f:
        while True:
            h = f.read(FRAME.size)
            if len(h) < FRAME.size:
                return
            t, n = FRAME.unpack(h)
            data = f.read(n)
            if len(data) < n:
                return
            yield t, data


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--speed", type=float, default=1.0, help="0 = as fast as possible")
    ap.add_argument("--until-seconds", type=float, default=None,
                    help="stop after this many seconds of recorded time")
    ap.add_argument("--port", type=int, default=FORWARD_PORT)
    a = ap.parse_args(argv)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t0 = wall0 = None
    sent = 0
    for t, data in frames(a.path):
        if t0 is None:
            t0, wall0 = t, time.monotonic()
        if a.until_seconds is not None and t - t0 > a.until_seconds:
            break
        if a.speed > 0:
            delay = (t - t0) / a.speed - (time.monotonic() - wall0)
            if delay > 0:
                time.sleep(delay)
        sock.sendto(data, (FORWARD_HOST, a.port))
        sent += 1
    print(f"sent {sent} datagrams")


if __name__ == "__main__":
    main()
