"""Live feed: receives the copy of every UDP datagram that the capture service
forwards to 127.0.0.1:20778, decodes it with the capture's OWN parser.py
(read-only import, never modified), and feeds LiveState.

Decoded packets are given the same flat shape the capture writes to
parsed.jsonl, so the state layer sees live and replayed data identically.
"""
import importlib.util
import os
import socket
import threading

from .state import WANTED, LiveState

FORWARD_HOST = "127.0.0.1"
FORWARD_PORT = 20778
# capture/ sits next to engineer/ (~/esports on the server, D:\RealF1-esports on the PC)
DEFAULT_PARSER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "capture", "parser.py")


def load_parser(path=DEFAULT_PARSER):
    spec = importlib.util.spec_from_file_location("f1_capture_parser", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def decode(parser, data):
    """Same steps as udp_capture.py: parse_header, then parse_packet()['body']."""
    header = parser.parse_header(data)
    if header.get("packet_name") not in WANTED:
        return header, None
    body = parser.parse_packet(data)["body"]
    pkt = {"packet_name": header.get("packet_name"), "packet_id": header.get("m_packetId"),
           "sessionTime": header.get("m_sessionTime"),
           "frameIdentifier": header.get("m_frameIdentifier")}
    pkt.update(body)
    return header, pkt


class LiveFeed:
    def __init__(self, state: LiveState, parser, host=FORWARD_HOST, port=FORWARD_PORT):
        self.state = state
        self.parser = parser
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # No SO_REUSEADDR on purpose: a second listener on this port would silently split
        # the packets with the first. Binding twice must fail loudly instead.
        self.sock.bind((host, port))
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.running = False
        self.thread = None
        self.session_uid = None
        self.received = 0
        self.errors = 0

    def run(self):
        self.running = True
        while self.running:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self.received += 1
            try:
                header, pkt = decode(self.parser, data)
                uid = header.get("m_sessionUID")
                # UID 0 packets are stray (capture shows all-zero-UID folders); they
                # must not wipe a live session.
                if uid and uid != self.session_uid:
                    if self.session_uid is not None:
                        self.state.reset()
                    self.session_uid = uid
                if pkt is not None:
                    self.state.ingest(pkt)
            except Exception:
                self.errors += 1

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        self.sock.close()
