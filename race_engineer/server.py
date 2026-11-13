"""The engineer as a background program: live feed from capture's forwarded copy
(127.0.0.1:20778) plus a small HTTP interface for the question tools.

  python3 -m race_engineer.server [--host 127.0.0.1] [--port 8777] [--pit-loss S] [--buffer S]

Endpoints (GET, JSON):
  /status
  /ask?tool=gap_to&driver=hamilton
  /ask?tool=tyre_status[&driver=hamilton]
  /ask?tool=pit_window[&pit_loss=S][&buffer=S]
  /ask?tool=weather_change
  /ask?tool=last_lap | laps | ai_deploy | reminders   (lap analysis while driving, see live_laps.py)

Binds to 127.0.0.1 by default. There is NO authentication, so do not bind it to a
network address without adding some.
"""
import argparse
import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .live import DEFAULT_PARSER, FORWARD_PORT, LiveFeed, load_parser
from .state import LiveState
from .tools import (DEFAULT_BUFFER_S, ToolError, damage_status, gap_to, pit_loss, pit_window, tyre_field,
                    tyre_status, weather_change)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8777


def _float(q, key, default):
    if key not in q:
        return default
    try:
        return float(q[key])
    except ValueError:
        raise ToolError(f"'{key}' must be a number.")


def run_tool(state, q, cfg):
    tool = q.get("tool")
    if tool == "gap_to":
        return gap_to(state, q.get("driver", ""))
    if tool == "tyre_status":
        return tyre_status(state, q.get("driver", "me"))
    if tool == "pit_window":
        return pit_window(state, _float(q, "pit_loss", cfg.get("pit_loss")),
                          _float(q, "buffer", cfg.get("buffer", DEFAULT_BUFFER_S)))
    if tool == "weather_change":
        return weather_change(state)
    if tool == "damage_status":
        return damage_status(state, q.get("driver", "me"))
    if tool == "pit_loss":
        return pit_loss(state)
    if tool == "tyre_field":
        return tyre_field(state)
    if tool == "last_lap":
        with state.lock:
            return state.live_laps.last()
    if tool == "laps":
        with state.lock:
            return state.live_laps.all()
    if tool == "ai_deploy":
        with state.lock:
            return state.live_laps.ai_deploy()
    if tool == "reminders":
        with state.lock:
            return state.live_laps.reminders()
    raise ToolError("Unknown tool. Use gap_to, tyre_status, pit_window, weather_change, damage_status, "
                    "pit_loss, tyre_field, last_lap, laps, ai_deploy or reminders.")


def status(state, feed):
    with state.lock:
        slot = state.player_slot()
        me = state.lap_data[slot] if slot is not None and slot < len(state.lap_data) else {}
        return {"ok": True, "ready": state.ready(), "session_time": state.session_time,
                "packets_in_state": dict(state.counts),
                "datagrams_received": feed.received if feed else None,
                "decode_errors": feed.errors if feed else None,
                "you": state.name_of(slot) if slot is not None and state.participants else None,
                "your_lap": me.get("currentLapNum"), "your_position": me.get("carPosition"),
                "drivers": [p["name"] for p in state.participants[: state._n()]],
                "lap_reports": len(state.live_laps.reports)}


def make_server(state, feed, host=DEFAULT_HOST, port=DEFAULT_PORT, cfg=None):
    cfg = cfg or {}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/status":
                    self._send(200, status(state, feed))
                elif u.path == "/ask":
                    self._send(200, run_tool(state, q, cfg))
                else:
                    self._send(404, {"error": "Not found. Use /status or /ask."})
            except ToolError as e:
                self._send(400, {"error": str(e)})
            except Exception:
                self._send(500, {"error": "Internal error."})

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--pit-loss", type=float, default=None)
    ap.add_argument("--buffer", type=float, default=DEFAULT_BUFFER_S)
    ap.add_argument("--parser", default=DEFAULT_PARSER)
    ap.add_argument("--feed-port", type=int, default=FORWARD_PORT,
                    help="UDP port for the live feed (the simulator uses a different one)")
    ap.add_argument("--lap-log", default=None, help="where lap reports are appended (default run/live_laps.txt)")
    ap.add_argument("--circuit-store", default=None,
                    help="per-circuit pit data file (default run/circuit_data.json; 'none' = do not store)")
    a = ap.parse_args(argv)

    from pathlib import Path
    from .circuit_store import DEFAULT as STORE
    store = None if a.circuit_store == "none" else Path(a.circuit_store) if a.circuit_store else STORE
    state = LiveState(lap_log=Path(a.lap_log) if a.lap_log else None, circuit_store_path=store)
    feed = LiveFeed(state, load_parser(a.parser), port=a.feed_port).start()
    httpd = make_server(state, feed, a.host, a.port, {"pit_loss": a.pit_loss, "buffer": a.buffer})
    stop = threading.Event()

    def shutdown(*_):
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"engineer: live feed on 127.0.0.1:{feed.port}, questions on http://{a.host}:{a.port}", flush=True)
    httpd.serve_forever()
    feed.stop()
    print("engineer: stopped", flush=True)


if __name__ == "__main__":
    main()
