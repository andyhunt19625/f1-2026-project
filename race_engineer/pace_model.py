"""AI pace model: how much faster do the AI get through a weekend, and what will qualifying look like?

For each race weekend (same weekendLinkIdentifier and track) and the same conditions (dry / wet):
- practice: the MEDIAN of all AI cars' best clean laps in each practice session (and the best AI lap), how much each session
  improved on the one before, in ms and in % (positive = quicker). If a session was restarted, the attempt with the quickest
  AI lap is used.
- prediction for qualifying = last practice pace x (1 - gain %). The gain % comes from the first level that has data:
    1. saved real gains from earlier weekends at THIS circuit (same conditions): the median of them
    2. saved real gains from at least 3 other weekends anywhere (same conditions): the median of them
    3. the same % as this weekend's practice improvements (mean of the steps), Andy's starting rule
- after qualifying: the real gain % (last practice before it to that qualifying session) is worked out, compared with what
  the model would have predicted from practice alone (other weekends' data only), and kept: it is derived from the pace store
  (run/pace_store.json), so it is saved for good, and written to run/pace_model.json.
Limits: Q3-style sessions with fewer cars have a different "median"; qualifying and sprint-shootout gains are kept apart by
session type once the type is known. Gains are percentages, which assume the track is the same speed for all cars.
"""
import json
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

PRACTICE = (1, 2, 3, 4)
QUALI_LIKE = (5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
MODEL_FILE = Path(__file__).resolve().parent.parent / "run" / "pace_model.json"
MIN_WEEKENDS_FOR_GLOBAL = 3


def pct(before, after):
    """Percent quicker (positive) going from `before` to `after` (both lap times)."""
    return round((before - after) / before * 100, 3)


def _usable(e):
    return e.get("weekend_id") is not None and e.get("ai_median_ms") and e.get("ai_best_ms")


def view(store, weekend_id, track_id):
    """(practice sessions, qualifying-like sessions) of one weekend, oldest first, one attempt per session type."""
    by = {}
    for e in store:
        if _usable(e) and e["weekend_id"] == weekend_id and e["track_id"] == track_id:
            by.setdefault((e["session_type"], e["conditions"]), []).append(e)      # wet and dry restarts are kept apart
    pick = {k: min(v, key=lambda e: e["ai_best_ms"]) for k, v in by.items()}
    prac = sorted((e for (t, _), e in pick.items() if t in PRACTICE), key=lambda e: e["folder"])
    quali = sorted((e for (t, _), e in pick.items() if t in QUALI_LIKE), key=lambda e: e["folder"])
    return prac, quali


def steps(prac):
    return [{"from": a["folder"], "to": b["folder"], "median_ms": a["ai_median_ms"] - b["ai_median_ms"],
             "best_ms": a["ai_best_ms"] - b["ai_best_ms"], "median_pct": pct(a["ai_median_ms"], b["ai_median_ms"]),
             "best_pct": pct(a["ai_best_ms"], b["ai_best_ms"])} for a, b in zip(prac, prac[1:])]


def gains(prac, quali, qtype=None):
    """Real practice-to-qualifying gains of one weekend (same conditions only)."""
    out = []
    for q in quali:
        if qtype is not None and q["session_type"] != qtype:
            continue
        before = [p for p in prac if p["folder"] < q["folder"] and p["conditions"] == q["conditions"]]
        if before:
            p = before[-1]
            out.append({"quali_type": q["session_type"], "conditions": q["conditions"], "from": p["folder"], "to": q["folder"],
                        "median_pct": pct(p["ai_median_ms"], q["ai_median_ms"]), "best_pct": pct(p["ai_best_ms"], q["ai_best_ms"]),
                        "median_ms": q["ai_median_ms"], "best_ms": q["ai_best_ms"]})
    return out


def all_gains(store, exclude=None):
    out = []
    for wk, tr in sorted({(e["weekend_id"], e["track_id"]) for e in store if e.get("weekend_id") is not None}):
        if (wk, tr) == exclude:
            continue
        prac, quali = view(store, wk, tr)
        out += [dict(g, weekend_id=wk, track_id=tr) for g in gains(prac, quali)]
    return out


def _level(n, label, med_pcts, best_pcts, last):
    pm, pb = st.median(med_pcts), st.median(best_pcts)
    return {"level": n, "label": label, "median_gain_pct": round(pm, 3), "best_gain_pct": round(pb, 3),
            "median_ms": round(last["ai_median_ms"] * (1 - pm / 100)), "best_ms": round(last["ai_best_ms"] * (1 - pb / 100))}


def predict(store, prac, track_id, exclude=None, qtype=None):
    """Prediction levels, best first, for the qualifying that follows `prac` (same-conditions practice, oldest first)."""
    if not prac:
        return []
    last, cond = prac[-1], prac[-1]["conditions"]
    hist = [g for g in all_gains(store, exclude) if g["conditions"] == cond and (qtype is None or g["quali_type"] == qtype)]
    same = [g for g in hist if g["track_id"] == track_id]
    out = []
    if same:
        out.append(_level(1, f"saved real gain from {len(same)} earlier weekend{'s' if len(same) != 1 else ''} at this circuit",
                          [g["median_pct"] for g in same],
                          [g["best_pct"] for g in same], last))
    if len({(g["weekend_id"], g["track_id"]) for g in hist}) >= MIN_WEEKENDS_FOR_GLOBAL:
        out.append(_level(2, f"median of {len(hist)} saved real gains at other circuits", [g["median_pct"] for g in hist],
                          [g["best_pct"] for g in hist], last))
    sp = steps(prac)
    if sp:
        out.append(_level(3, f"same % as this weekend's practice improvements ({len(sp)} step{'s' if len(sp) != 1 else ''}, mean)",
                          [st.mean(s["median_pct"] for s in sp)], [st.mean(s["best_pct"] for s in sp)], last))
    return out


def build(store, entry):
    """The model for one report: practice table, predictions, and (for a qualifying report) the actual result and errors."""
    wk, tr = entry["weekend_id"], entry["track_id"]
    prac, quali = view(store, wk, tr)
    is_quali = entry["session_type"] in QUALI_LIKE
    cond = entry["conditions"]
    prac_c = [p for p in prac if p["conditions"] == cond and p["folder"] <= entry["folder"]]
    sp = steps(prac_c)
    rows = []
    for i, p in enumerate(prac_c):
        rows.append({"folder": p["folder"], "session_type": p["session_type"], "median_ms": p["ai_median_ms"],
                     "best_ms": p["ai_best_ms"], "ai_cars": p.get("ai_n"), "step": sp[i - 1] if i else None})
    levels = predict(store, prac_c, tr, exclude=(wk, tr) if is_quali else None,
                     qtype=entry["session_type"] if is_quali else None)
    out = {"conditions": cond, "practice": rows, "levels": levels, "headline": levels[0] if levels else None, "actual": None,
           "note": None if levels else "Needs two practice sessions in the same conditions, or a saved weekend, to predict."}
    if is_quali:
        same = [q for q in quali if q["session_type"] == entry["session_type"] and q["conditions"] == cond]
        g = [x for x in gains(prac, quali, entry["session_type"]) if same and x["to"] == same[0]["folder"]]
        if g:
            a = g[0]
            out["actual"] = dict(a, errors=[{"level": l["level"], "median_error_ms": l["median_ms"] - a["median_ms"],
                                             "best_error_ms": l["best_ms"] - a["best_ms"]} for l in levels],
                                 saved=f"{a['median_pct']}% (median) / {a['best_pct']}% (best AI lap) quicker than the last practice; "
                                       f"saved for this circuit in {a['conditions']} conditions.")
    return out


def save(store, path=MODEL_FILE):
    """Write every saved real gain, derived from the pace store, so it can be read and backed up."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                                          "saved_real_gains": all_gains(store)}, indent=1), encoding="utf-8")
    except OSError:
        pass
