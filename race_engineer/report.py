"""One command, one read of the session: your laps against your best, and you against the
faster cars by corner type, with setup differences.

  python -m race_engineer.report PATH/parsed.jsonl [--segment-m 100]

The first run reads parsed.jsonl once and saves laps_cache.pkl.gz beside it (only the lap and
telemetry fields the tools use). Later runs load that cache in about a second. The cache is
rebuilt automatically if parsed.jsonl is newer or the cache format changes. Read-only on the
session data; the only file written is the cache.
"""
import argparse
import gzip
import pickle
from pathlib import Path

from . import compare, lap_analysis

CACHE_NAME = "laps_cache.pkl.gz"
CACHE_VERSION = 2   # bump when LapBuilder's output changes


def load_cached(path):
    path = Path(path)
    cache = path.with_name(CACHE_NAME)
    if cache.is_file() and cache.stat().st_mtime >= path.stat().st_mtime:
        try:
            with gzip.open(cache, "rb") as f:
                blob = pickle.load(f)
            if blob.get("version") == CACHE_VERSION:
                return blob["data"], True
        except (OSError, EOFError, pickle.UnpicklingError, KeyError):
            pass
    data = compare.load_all(str(path))
    try:
        with gzip.open(cache, "wb", compresslevel=3) as f:
            pickle.dump({"version": CACHE_VERSION, "data": data}, f, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass
    return data, False


def run(path, seg_m=100.0):
    data, cached = load_cached(path)
    if not data["track_length"]:
        data["track_length"] = max((s[0] for laps in data["laps"] for l in laps.values()
                                    for s in l["samples"]), default=0)
    me = data["player"]
    length = data["track_length"]
    out = [f"Driver: {data['names'][me]}. Track length: {length} m. "
           f"({'from cache' if cached else 'read the full session file; cache saved'})", "",
           "=== Your laps ===",
           lap_analysis.report(lap_analysis.analyse(data["laps"][me], length, seg_m)), "",
           "=== Against the faster cars ===",
           compare.report(compare.analyse(data))]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--segment-m", type=float, default=100.0)
    a = ap.parse_args(argv)
    print(run(a.path, a.segment_m))


if __name__ == "__main__":
    main()
