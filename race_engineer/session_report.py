"""After-session report: where you are quicker and slower than the best AI lap, on a track map, with
corner types, setup differences (rule of thumb, labelled), battery, fuel, events, and an AI pace model
for the race weekend.

  python -m race_engineer.session_report SESSION_FOLDER [--out DIR]

Writes <out>/<folder>.html (default D:\\RealF1-esports\\reports) and <out>/<folder>.json, and records the
session's pace in run/pace_store.json for the weekend model. Read-only on the session data.
"""
import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from . import compare, ers_analysis as ers, events as ev
from .lap_analysis import compare as seg_compare, complete, fmt_ms
from .session_digest import as_compare_data, load
from .session_reader import lap_label

ROOT = Path(__file__).resolve().parent.parent
PACE_STORE = ROOT / "run" / "pace_store.json"
DEFAULT_OUT = ROOT.parent / "reports"
TRACKS = {0: "Melbourne"}          # only names seen in Andy's data
SESSION_NAMES = {1: "Practice 1", 2: "Practice 2", 3: "Practice 3", 4: "Short practice", 5: "Qualifying 1",
                 6: "Qualifying 2", 7: "Qualifying 3", 8: "Short qualifying", 9: "One-shot qualifying",
                 15: "Race", 16: "Race 2", 17: "Race 3", 18: "Time trial"}
PRACTICE, QUALI = (1, 2, 3, 4), (5, 6, 7, 8, 9)


def _best(laps, length, need_active=False):
    g = {k: l for k, l in laps.items() if complete(l, length) and (not need_active or ers.ers_active(l))}
    if not g:
        return None, None
    k = min(g, key=lambda x: g[x]["time_ms"])
    return k, g[k]


def conditions(digest):
    codes = [w[1] for w in digest["weather"] if w[1] is not None]
    if not codes:
        return "unknown"
    return "wet" if sum(1 for c in codes if c >= 3) > len(codes) / 2 else "dry"


def pace_entry(folder, digest):
    m, length = digest["meta"], digest["meta"]["track_length"]
    me = m["player"]
    ai = []
    for slot, laps in enumerate(digest["laps"]):
        if slot == me:
            continue
        k, l = _best(laps, length)
        if l:
            ai.append(l["time_ms"])
    ai.sort()
    mk, ml = _best(digest["laps"][me], length) if me is not None else (None, None)
    return {"folder": folder, "weekend_id": m["weekend_id"], "track_id": m["track_id"],
            "session_type": m["session_type"], "conditions": conditions(digest),
            "ai_best_ms": ai[0] if ai else None,
            "ai_top5_median_ms": statistics.median(ai[:5]) if ai else None,
            "my_best_ms": ml["time_ms"] if ml else None, "ai_difficulty": m["ai_difficulty"]}


def store_pace(entry, path=PACE_STORE):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = []
    data = [e for e in data if e.get("folder") != entry["folder"]] + [entry]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def predict_quali(entry, store):
    """Predict the AI's best qualifying lap from practice. Two methods, each says what it used."""
    same = [e for e in store if e["weekend_id"] == entry["weekend_id"] and e["track_id"] == entry["track_id"]
            and e["conditions"] == entry["conditions"] and e.get("ai_best_ms")]
    prac = sorted([e for e in same if e["session_type"] in PRACTICE], key=lambda e: e["folder"])
    out = {"conditions": entry["conditions"], "methods": []}
    if not prac:
        return out
    last = prac[-1]["ai_best_ms"]
    # A: other weekends: qualifying AI best / last practice AI best (same conditions)
    ratios = []
    for wk in {e["weekend_id"] for e in store if e["weekend_id"] != entry["weekend_id"]}:
        w = [e for e in store if e["weekend_id"] == wk and e["conditions"] == entry["conditions"] and e.get("ai_best_ms")]
        p = sorted([e for e in w if e["session_type"] in PRACTICE], key=lambda e: e["folder"])
        q = [e for e in w if e["session_type"] in QUALI]
        if p and q:
            ratios.append(min(e["ai_best_ms"] for e in q) / p[-1]["ai_best_ms"])
    if ratios:
        r = statistics.median(ratios)
        out["methods"].append({"method": f"other weekends ({len(ratios)}): qualifying is {100 * (1 - r):.2f}% quicker "
                                         f"than the last practice", "ai_quali_ms": round(last * r)})
    # B: this weekend's trend between practice sessions, one more step
    if len(prac) >= 2:
        steps = [b["ai_best_ms"] - a["ai_best_ms"] for a, b in zip(prac, prac[1:])]
        step = statistics.mean(steps)
        out["methods"].append({"method": f"this weekend's practice trend ({len(prac)} sessions, "
                                         f"{step / 1000:+.3f} s per session)", "ai_quali_ms": round(last + step)})
    if not out["methods"]:
        out["note"] = "Not enough data yet: needs two practice sessions in the same conditions, or a stored weekend."
    return out


def build_report(folder, digest):
    m, length = digest["meta"], digest["meta"]["track_length"]
    me, names = m["player"], m["names"]
    data = as_compare_data(digest)
    my_k, my_l = _best(digest["laps"][me], length)
    res = {"folder": Path(folder).name, "session": SESSION_NAMES.get(m["session_type"], f"session type {m['session_type']}"),
           "track": TRACKS.get(m["track_id"], f"track {m['track_id']}"), "conditions": conditions(digest),
           "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    if my_l is None:
        res["error"] = "You have no clean complete lap in this session."
        return res
    cmp = compare.analyse(data, max_cars=1)
    ai_slot = None
    best_ai = None
    for slot, laps in enumerate(digest["laps"]):
        if slot == me:
            continue
        k, l = _best(laps, length)
        if l and (best_ai is None or l["time_ms"] < best_ai[2]["time_ms"]):
            best_ai = (slot, k, l)
    res.update({"you": names[me], "your_lap": lap_label(my_k), "your_ms": my_l["time_ms"]})
    if best_ai:
        ai_slot, ai_k, ai_l = best_ai
        segs = seg_compare(my_l, ai_l, length, 100)
        res.update({"ai": names[ai_slot], "ai_lap": lap_label(ai_k), "ai_ms": ai_l["time_ms"],
                    "segments": [{"from_m": s["from_m"], "to_m": s["to_m"], "gain_ms": -s["loss_ms"],
                                  "you_min_kmh": (s["you"] or {}).get("min_speed"),
                                  "ai_min_kmh": (s["best"] or {}).get("min_speed")} for s in segs]})
    pts, last = [], -1e9
    for d, x, z in sorted(p for p in my_l.get("pos", []) if p[1] is not None):
        if d - last >= 10:
            pts.append([round(d, 1), round(x, 2), round(z, 2)])
            last = d
    res["points"] = pts
    res["track_length"] = length
    if "versus" in cmp and cmp["versus"]:
        v = cmp["versus"][0]
        res["by_type_ms"] = v["loss_by_type_ms"]
        res["corners"] = sorted(v["corners"], key=lambda c: -c["loss_ms"])[:6]
        res["setup_differences"] = v["setup_differences"]
        worst = max((k for k in ("slow", "medium", "fast", "straight") if k in v["loss_by_type_ms"]),
                    key=lambda k: v["loss_by_type_ms"][k])
        if v["loss_by_type_ms"][worst] > 10:      # only when you actually lose time somewhere
            res["rule_of_thumb"] = {"weakest": worst, "text": compare.GENERAL_GUIDANCE[worst]}
        else:
            res["rule_of_thumb"] = {"weakest": None, "text": "You were not slower than this AI lap in any type of corner or on the straights."}
        res["compare_note"] = cmp.get("note")
    if m["session_type"] != 18:                     # no battery deployment in time trial
        res["battery_you"] = ers.lap_summary(my_l)
        if best_ai:
            _, a = _best(digest["laps"][ai_slot], length, need_active=True)
            res["battery_ai"] = ers.lap_summary(a) if a else None
    fuel = []
    for k in sorted(digest["laps"][me]):
        l = digest["laps"][me][k]
        if l.get("fuel_start") is not None:
            fuel.append({"lap": lap_label(k), "fuel_start_kg": round(l["fuel_start"], 2), "time_ms": l["time_ms"]})
    res["fuel"] = fuel
    res["events"] = [{"t": round(e[0] or 0, 1), "lap": e[3] if len(e) > 3 else None, "code": e[1],
                      "text": ev.describe(e[1], e[2], names, me)}
                     for e in digest["events"] if ev.describe(e[1], e[2], names, me)]
    res["safety_car"] = [{"t": round(x[0] or 0, 1), "lap": x[2] if len(x) > 2 else None,
                          "status": ev.SAFETY_CAR.get(x[1], x[1])} for x in digest["safety_car"]]
    return res


PAGE = Path(__file__).resolve().parent / "report_page.html"


def write(res, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{res['folder']}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    html = PAGE.read_text(encoding="utf-8").replace("__DATA__", json.dumps(res, separators=(",", ":")))
    p = out_dir / f"{res['folder']}.html"
    p.write_text(html, encoding="utf-8")
    return p


def run(folder, out_dir=DEFAULT_OUT, pace_store=PACE_STORE):
    folder = Path(folder)
    parsed = folder / "parsed.jsonl" if folder.is_dir() else folder
    digest, cached = load(parsed)
    res = build_report(parsed.parent.name, digest)
    res["folder"] = parsed.parent.name
    entry = pace_entry(parsed.parent.name, digest)
    store = store_pace(entry, pace_store)
    res["pace"] = {"this_session": entry, "weekend": [e for e in store if e["weekend_id"] == entry["weekend_id"]],
                   "prediction": predict_quali(entry, store) if entry["session_type"] in PRACTICE else None}
    res["digest_cached"] = cached
    return res, write(res, out_dir)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    res, path = run(a.folder, a.out)
    gap = (res.get("your_ms") or 0) - (res.get("ai_ms") or 0)
    print(f"{res['session']} at {res['track']} ({res['conditions']}): you {fmt_ms(res['your_ms']) if res.get('your_ms') else '-'}"
          + (f", best AI {res['ai'].title()} {fmt_ms(res['ai_ms'])} ({gap / 1000:+.3f} s)" if res.get("ai_ms") else ""))
    p = (res.get("pace") or {}).get("prediction")
    if p:
        for mth in p["methods"]:
            print(f"  AI qualifying prediction: {fmt_ms(mth['ai_quali_ms'])} from {mth['method']}")
        if p.get("note"):
            print("  " + p["note"])
    print(f"Report: {path}")


if __name__ == "__main__":
    main()
