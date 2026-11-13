"""Map layers for the session report: one extra view of the track per topic, you against the best AI lap.

  battery       where you and the AI use the battery (MGU-K kW, 50 m sections)
  braking       every braking zone: where you start braking against where the AI does, entry speed, peak pressure
  acceleration  every corner exit: where you reach full throttle, and your speed 100 m later, against the AI
  fuel          where you and the AI burn fuel (grams per 50 m): the saving points
  tyres         where the tyres wear along the lap (each section's share of that lap's wear)

Each layer is a dict. "available" is False, with a "note", when the data needed was not recorded (fuel and tyre
wear along the lap are recorded from 13 Nov 2026 evening onward; older sessions only have per-lap values).
All values come from the two laps given (your best clean lap and the best clean AI lap with real battery data).
"""
from . import ers_analysis as ers
from .lap_analysis import clean_series

STEP = 50          # metres per section for the section layers


def _interp(series, d, idx):
    """Linear value of column idx of (dist, ...) tuples at distance d; None if the column is missing."""
    if not series:
        return None
    if d <= series[0][0]:
        v = series[0][idx]
        return v
    for a, b in zip(series, series[1:]):
        if a[0] <= d <= b[0]:
            if a[idx] is None or b[idx] is None:
                return None
            return a[idx] + (b[idx] - a[idx]) * (d - a[0]) / (b[0] - a[0]) if b[0] > a[0] else a[idx]
    return series[-1][idx]


def _sections(length, step=STEP):
    lo = 0.0
    while lo < length - step * 0.2:
        yield lo, min(lo + step, length)
        lo += step


def _pair(layer_id, title, unit, higher, you_vals, ai_vals, length, explain):
    segs = []
    for (lo, hi), a, b in zip(_sections(length), you_vals, ai_vals):
        if a is not None and b is not None:
            segs.append({"from_m": round(lo), "to_m": round(hi), "you": round(a, 3), "ai": round(b, 3)})
    if not segs:
        return {"id": layer_id, "title": title, "available": False, "note": "not recorded in this session"}
    return {"id": layer_id, "title": title, "kind": "pair", "unit": unit, "higher": higher, "available": True,
            "segments": segs, "explain": explain}


def battery(you, ai, length):
    if not you or not ai:
        return {"id": "battery", "title": "Battery deployment", "available": False, "note": "no AI lap with real battery data"}
    n = len(list(_sections(length)))
    yk, ak = ers.kw_bins(you, length, STEP), ers.kw_bins(ai, length, STEP)
    return _pair("battery", "Battery deployment", "kW", "neutral", yk[:n], ak[:n], length,
                 "Average power the electric motor sends to the wheels in each 50 m section (zero while harvesting).")


def _burn(lap, length):
    s = sorted((e for e in lap.get("ers", []) if len(e) > 5 and e[5] is not None), key=lambda e: (e[0], e[1]))
    if len(s) < 20:
        return None
    out = []
    for lo, hi in _sections(length):
        a, b = _interp(s, lo, 5), _interp(s, hi, 5)
        out.append(None if a is None or b is None else max(0.0, (a - b) * 1000))      # grams
    return out


def fuel(you, ai, length):
    y, a = (_burn(you, length), _burn(ai, length)) if you and ai else (None, None)
    if not y or not a:
        return {"id": "fuel", "title": "Fuel use by section", "available": False,
                "note": "fuel along the lap is recorded from the next session onward"}
    return _pair("fuel", "Fuel use by section", "g", "bad", y, a, length,
                 "Fuel burned in each 50 m section, in grams. Red = you burn more than the AI there: the saving points.")


def _wear_share(lap, length):
    ws = sorted(lap.get("wear_s") or [])
    if len(ws) < 20:
        return None
    avg = [(d, sum(w) / len(w)) for d, w in ws]
    total = avg[-1][1] - avg[0][1]
    if total < 0.2 or len({round(a, 3) for _, a in avg}) < 8:          # too coarse to show where it happens
        return None
    out = []
    for lo, hi in _sections(length):
        a, b = _interp(avg, lo, 1), _interp(avg, hi, 1)
        out.append(None if a is None or b is None else max(0.0, (b - a) / total * 100))
    return out


def tyres(you, ai, length):
    y, a = (_wear_share(you, length), _wear_share(ai, length)) if you and ai else (None, None)
    if not y or not a:
        return {"id": "tyres", "title": "Tyre wear by section", "available": False,
                "note": "tyre wear along the lap is recorded from the next session onward"}
    return _pair("tyres", "Tyre wear by section", "% of lap wear", "bad", y, a, length,
                 "Each 50 m section's share of the wear the tyres picked up over the lap (average of the four tyres). "
                 "Red = a bigger share than the AI's in that section.")


def brake_zones(samples):
    """Braking zones of a lap: where braking starts (brake > 10%), the speed then, the peak pressure, the slowest speed."""
    zones, cur = [], None
    for x in clean_series(samples):
        d, speed, brake = x[0], x[2], x[4]
        if brake > 0.1:
            if cur is None:
                cur = {"start": d, "entry": speed, "peak": brake, "min": speed, "end": d}
            else:
                cur["peak"], cur["min"], cur["end"] = max(cur["peak"], brake), min(cur["min"], speed), d
        elif cur is not None:
            zones.append(cur)
            cur = None
    if cur is not None:
        zones.append(cur)
    return [z for z in zones if z["end"] - z["start"] >= 15 or z["peak"] >= 0.5]


def braking(you, ai):
    if not you or not ai:
        return {"id": "braking", "title": "Braking points", "available": False, "note": "no AI lap to compare with"}
    yz, az = brake_zones(you["samples"]), brake_zones(ai["samples"])
    used, items = set(), []
    for z in yz:
        best = min((i for i in range(len(az)) if i not in used and abs(az[i]["start"] - z["start"]) <= 200),
                   key=lambda i: abs(az[i]["start"] - z["start"]), default=None)
        o = az[best] if best is not None else None
        if best is not None:
            used.add(best)
        items.append({"you_m": round(z["start"]), "ai_m": round(o["start"]) if o else None,
                      "diff_m": round(z["start"] - o["start"]) if o else None,
                      "you_entry_kmh": z["entry"], "ai_entry_kmh": o["entry"] if o else None,
                      "you_peak_pct": round(z["peak"] * 100), "ai_peak_pct": round(o["peak"] * 100) if o else None,
                      "you_min_kmh": z["min"], "ai_min_kmh": o["min"] if o else None})
    for i, o in enumerate(az):
        if i not in used:
            items.append({"you_m": None, "ai_m": round(o["start"]), "diff_m": None, "you_entry_kmh": None,
                          "ai_entry_kmh": o["entry"], "you_peak_pct": None, "ai_peak_pct": round(o["peak"] * 100),
                          "you_min_kmh": None, "ai_min_kmh": o["min"]})
    items.sort(key=lambda x: x["you_m"] if x["you_m"] is not None else x["ai_m"])
    return {"id": "braking", "title": "Braking points", "kind": "markers", "available": True, "items": items,
            "explain": "Where each braking zone starts (brake over 10%). A positive difference means you brake later than the AI. A dash means "
            "that lap has no braking over 10% there."}


def acceleration(corners, you, ai):
    """Corner exits: where full throttle is reached, and both laps' speed at the SAME track positions after the apex
    (150 m and 300 m after your apex), so the speeds can be compared fairly."""
    if not corners or not you or not ai:
        return {"id": "acceleration", "title": "Acceleration points", "available": False, "note": "no AI lap to compare with"}
    ys, as_ = clean_series(you["samples"]), clean_series(ai["samples"])
    yz, az = brake_zones(you["samples"]), brake_zones(ai["samples"])
    yv, av = [(s[0], s[2]) for s in ys], [(s[0], s[2]) for s in as_]
    length = max(ys[-1][0], as_[-1][0]) if ys and as_ else 0
    at = lambda series, d: None if d is None else (lambda v: None if v is None else round(v))(_interp(series, min(d, length), 1))
    items = []
    for c in corners:
        y, t = c["you"], c["them"]
        yf, tf = y.get("full_throttle_at_m"), t.get("full_throttle_at_m")
        if not yf and not tf:
            continue
        ref = y.get("apex_at_m") or t.get("apex_at_m")
        # a speed read inside the NEXT braking zone is not an exit speed: leave it out (short gaps between corners)
        nxt = [min((z["start"] for z in zones if z["start"] > ref + 5), default=None) for zones in (yz, az)] if ref else []
        nxt = [n for n in nxt if n is not None]
        limit = min(nxt) - 10 if nxt else None
        ok = lambda k: bool(ref) and (limit is None or ref + k <= limit)
        items.append({"corner": c["corner"], "type": c["type"], "you_m": yf, "ai_m": tf,
                      "diff_m": (yf - tf) if yf and tf else None, "you_apex_kmh": y.get("apex_speed"),
                      "ai_apex_kmh": t.get("apex_speed"), "ref_m": ref,
                      "you_150_kmh": at(yv, ref + 150) if ok(150) else None, "ai_150_kmh": at(av, ref + 150) if ok(150) else None,
                      "you_300_kmh": at(yv, ref + 300) if ok(300) else None, "ai_300_kmh": at(av, ref + 300) if ok(300) else None})
    if not items:
        return {"id": "acceleration", "title": "Acceleration points", "available": False, "note": "no corner exits found"}
    return {"id": "acceleration", "title": "Acceleration points", "kind": "markers", "available": True, "items": items,
            "explain": "Where full throttle is reached after each corner (a negative difference means you are on full throttle "
                       "earlier than the AI), and both laps' speed at the same places 150 m and 300 m after your apex. "
                       "A dash means the next braking zone starts before that point. Reaching full throttle earlier is not the same as leaving "
                       "the corner faster."}


def build(you, ai, ai_active, corners, length):
    """All layers. you / ai: lap dicts (best clean laps); ai_active: the AI lap with real battery data; corners: compare's list."""
    return {"battery": battery(you, ai_active, length), "braking": braking(you, ai),
            "acceleration": acceleration(corners, you, ai), "fuel": fuel(you, ai, length), "tyres": tyres(you, ai, length)}
