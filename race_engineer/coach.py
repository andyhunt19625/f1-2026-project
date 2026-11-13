"""Coaching answers from the live data: where am I losing time, and should I deploy the battery now?

where_losing(state, ref="best")
  Compares your last completed clean lap with your best clean lap (ref="best") or with the best AI lap (ref="ai"), in 100 m
  sections, and for the three biggest losses says what the data shows and the usual fix:
    - you started braking EARLIER than the reference            -> "brake N m later"
    - you braked where the reference lap did not brake          -> "lift instead of braking"
    - your slowest speed in the section is lower                -> "carry N km/h more through it"
    - you reached full throttle later                           -> "get on full throttle N m earlier"
  The cause is read from your own numbers (braking start, slowest speed, full-throttle point); it says what differed, and the fix
  is the usual rule of thumb, not a measured result. Corners are named by the braking zone's order in the lap and its distance
  (the game's own turn numbers are not in the data).

deploy_more(state, ref="best")
  "Deploy more out of braking zone 5": your last clean lap's battery power on each corner exit (from the end of a braking zone to
  the next braking zone, at most 250 m) against your best lap, or the best AI lap with real battery data (ref="ai"). Lists the
  exits where the reference sent clearly more power to the wheels.

deploy_now(state)
  Where you are on the lap and your battery now, against where YOUR best lap deployed: inside one of its deploy zones, or how far to
  the next one, and whether your battery is above or below your best lap's at this point. It follows your best lap's plan; it does
  not work out a better one.
"""
from . import ers_analysis as ers
from . import report_layers
from .lap_analysis import compare, complete, fmt_ms
from .session_reader import lap_label
from .tools import ToolError

MIN_LOSS_MS = 40         # ignore sections that lost less than this (my choice)
LOW_BATTERY_MJ = 0.3     # below this there is little left to deploy (my choice)


def _me(state):
    me = state.player_slot()
    ll = state.live_laps
    if me is None:
        raise ToolError("Cannot tell which car is yours yet.")
    if me >= len(ll.builders) or not ll.length:
        raise ToolError("No lap data yet.")
    return me, ll.builders[me].laps, ll.length


def _zone_name(zones, lo, hi):
    """Name of the braking zone (counted in order along the lap) that starts in or just around the section."""
    near = [(i, z) for i, z in enumerate(zones, 1) if lo - 50 <= z["start"] < hi + 50]
    if near:
        i, z = min(near, key=lambda t: abs(t[1]["start"] - (lo + hi) / 2))
        return f"braking zone {i} ({round(z['start'])} m)"
    return f"{round(lo)}-{round(hi)} m"


ZONE_MATCH_M = 100      # a braking zone of yours and one of the reference lap are the same corner within this (my choice)


def _my_zone(yz, lo, hi):
    """Your braking zone that starts in or just around the section (nearest to its middle), else None."""
    near = [z for z in yz if lo - 50 <= z["start"] < hi + 50]
    return min(near, key=lambda z: abs(z["start"] - (lo + hi) / 2)) if near else None


def advise(seg, rz, yz):
    """What differed in one 100 m section (you against the reference) and the usual fix. Returns (place, what, fix).
    rz / yz: braking zones of the reference lap and of your lap (report_layers.brake_zones)."""
    y, r = seg["you"] or {}, seg["best"] or {}
    place = _zone_name(rz, seg["from_m"], seg["to_m"])
    mine = _my_zone(yz, seg["from_m"], seg["to_m"])
    if mine is not None:
        match = min((z for z in rz if abs(z["start"] - mine["start"]) <= ZONE_MATCH_M),
                    key=lambda z: abs(z["start"] - mine["start"]), default=None)
        if match is None:
            return place, "you braked here and the reference lap did not", "lift instead of braking"
        early = match["start"] - mine["start"]
        if early >= 5:
            return place, f"you started braking {round(early)} m earlier", f"brake {round(early)} m later"
    if y.get("min_speed") is not None and r.get("min_speed") is not None and r["min_speed"] - y["min_speed"] >= 3:
        d = r["min_speed"] - y["min_speed"]
        return place, f"your slowest speed is {round(d)} km/h lower", f"carry {round(d)} km/h more through it"
    yt, rt = y.get("full_throttle_at_m"), r.get("full_throttle_at_m")
    if yt is not None and rt is not None and yt - rt >= 10:
        return place, f"you reached full throttle {round(yt - rt)} m later", f"get on full throttle {round(yt - rt)} m earlier"
    return place, "no single cause stands out in braking, speed or throttle", "smooth out the whole section"


def where_losing(state, ref="best"):
    with state.lock:
        me, laps, length = _me(state)
        good = {k: l for k, l in laps.items() if complete(l, length)}
        if not good:
            raise ToolError("No clean lap of yours yet.")
        last_k = max(good)
        if ref == "ai":
            ai = None
            for slot, b in enumerate(state.live_laps.builders):
                if slot != me:
                    _, l = ers.best_clean(b.laps, length)
                    if l and (ai is None or l["time_ms"] < ai["time_ms"]):
                        ai = l
            if ai is None:
                raise ToolError("No clean AI lap yet.")
            ref_lap, ref_name = ai, "the best AI lap"
        else:
            if len(good) < 2:
                raise ToolError("Need two clean laps of yours first.")
            best_k = min(good, key=lambda k: good[k]["time_ms"])
            if best_k == last_k:
                return {"answer": f"Your last lap, {fmt_ms(good[last_k]['time_ms'])}, is your best so far. "
                                  f"Nothing lost against it.", "losses": [], "ref": "your best lap"}
            ref_lap, ref_name = good[best_k], "your best lap"
        last = good[last_k]
        gap = last["time_ms"] - ref_lap["time_ms"]
        zones = report_layers.brake_zones(ref_lap["samples"])
        yz = report_layers.brake_zones(last["samples"])
        segs = sorted(compare(last, ref_lap, length, 100), key=lambda s: -s["loss_ms"])
        rows, parts = [], []
        for s in segs[:3]:
            if s["loss_ms"] < MIN_LOSS_MS:
                continue
            place, what, fix = advise(s, zones, yz)
            rows.append({"from_m": s["from_m"], "to_m": s["to_m"], "loss_ms": s["loss_ms"], "place": place, "what": what,
                         "fix": fix})
            parts.append(f"{place}: lost {s['loss_ms'] / 1000:.2f} s, {what}. Try to {fix}")
        head = (f"Last lap {lap_label(last_k)} was {gap / 1000:+.3f} s against {ref_name}." if gap
                else f"Last lap matched {ref_name}.")
        text = head + (" Biggest losses: " + "; ".join(parts) + "." if parts else " No section lost more than "
                                                                                 f"{MIN_LOSS_MS / 1000:.2f} s.")
        return {"answer": text, "ref": ref_name, "gap_ms": gap, "losses": rows,
                "note": "What differed is read from your numbers; the fix is the usual rule of thumb, not measured."}


def deploy_now(state):
    with state.lock:
        me, laps, length = _me(state)
        if me >= len(state.car_status) or me >= len(state.lap_data):
            raise ToolError("No live car data yet.")
        pos = state.lap_data[me].get("lapDistance")
        store = (state.car_status[me].get("ersStoreEnergy") or 0) / 1e6
        good = {k: l for k, l in laps.items() if complete(l, length) and l.get("ers")}
        if pos is None or pos < 0:
            raise ToolError("You are not on a timed lap yet.")
        if not good:
            raise ToolError("Need one clean lap of yours first: the plan comes from your best lap.")
        best = good[min(good, key=lambda k: good[k]["time_ms"])]
        zones = ers.zones(ers.kw_bins(best, length, 50), 50)
        if not zones:
            raise ToolError("Your best lap shows no battery deployment to follow.")
        here = next((z for z in zones if z["from_m"] <= pos <= z["to_m"]), None)
        series = ers.ers_series(best)
        best_store = ers.store_at(series, pos)
        budget = ""
        if best_store is not None:
            d = store - best_store / 1e6
            budget = (f" Your battery is {abs(d):.2f} MJ {'above' if d >= 0 else 'below'} your best lap's at this point "
                      f"({store:.2f} MJ now).")
        if here:
            if store < LOW_BATTERY_MJ:
                text = (f"Battery nearly empty ({store:.2f} MJ), little to deploy. Your best lap deployed about "
                        f"{here['avg_kw']} kW from {here['from_m']} to {here['to_m']} m.")
                action = "empty"
            else:
                text = (f"Deploy now. Your best lap used about {here['avg_kw']} kW here, until {here['to_m']} m.{budget}")
                action = "deploy"
            return {"answer": text, "action": action, "lap_distance_m": round(pos), "zone": here, "battery_mj": round(store, 3)}
        ahead = [z for z in zones if z["from_m"] > pos]
        nxt = ahead[0] if ahead else zones[0]
        dist = (nxt["from_m"] - pos) if ahead else (length - pos + nxt["from_m"])
        text = (f"Hold. Next deploy zone in {round(dist)} m ({nxt['from_m']}-{nxt['to_m']} m, about {nxt['avg_kw']} kW on "
                f"your best lap).{budget}")
        return {"answer": text, "action": "hold", "lap_distance_m": round(pos), "next_zone": nxt,
                "distance_to_zone_m": round(dist), "battery_mj": round(store, 3)}


MORE_KW = 20.0          # an exit counts when the reference averages this many kW more than you (my choice)
EXIT_MAX_M = 250        # corner exit window after the end of a braking zone, at most (my choice)


def deploy_more(state, ref="best"):
    with state.lock:
        me, laps, length = _me(state)
        good = {k: l for k, l in laps.items() if complete(l, length) and l.get("ers") and ers.ers_active(l)}
        if not good:
            raise ToolError("No clean lap of yours with battery data yet.")
        last_k = max(good)
        if ref == "ai":
            ref_lap, ref_name = None, "the best AI lap"
            for slot, b in enumerate(state.live_laps.builders):
                if slot != me:
                    _, l = ers.best_clean(b.laps, length, need_active=True)
                    if l and (ref_lap is None or l["time_ms"] < ref_lap["time_ms"]):
                        ref_lap = l
            if ref_lap is None:
                raise ToolError("No clean AI lap with real battery data yet.")
        else:
            if len(good) < 2:
                raise ToolError("Need two clean laps of yours with battery data first.")
            best_k = min(good, key=lambda k: good[k]["time_ms"])
            if best_k == last_k:
                return {"answer": "Your last lap is your best so far, so there is nothing to deploy more against.",
                        "exits": [], "ref": "your best lap"}
            ref_lap, ref_name = good[best_k], "your best lap"
        last = good[last_k]
        zs = report_layers.brake_zones(ref_lap["samples"])
        yk, rk = ers.kw_bins(last, length, 50), ers.kw_bins(ref_lap, length, 50)
        rows = []
        for i, z in enumerate(zs, 1):
            end = z["end"]
            nxt = zs[i]["start"] if i < len(zs) else length
            stop = min(end + EXIT_MAX_M, nxt, length)
            idx = [j for j in range(len(yk)) if end <= j * 50 + 25 < stop and yk[j] is not None and rk[j] is not None]
            if not idx:
                continue
            y_avg = sum(yk[j] for j in idx) / len(idx)
            r_avg = sum(rk[j] for j in idx) / len(idx)
            if r_avg - y_avg >= MORE_KW:
                rows.append({"zone": i, "from_m": round(end), "to_m": round(stop), "you_kw": round(y_avg),
                             "ref_kw": round(r_avg), "diff_kw": round(r_avg - y_avg)})
        rows.sort(key=lambda r: -r["diff_kw"])
        rows = rows[:3]
        if not rows:
            text = f"No corner exit where {ref_name} deployed {MORE_KW:.0f} kW or more than you on lap {lap_label(last_k)}."
        else:
            text = (f"Deploy more against {ref_name}: " + "; ".join(
                f"out of braking zone {r['zone']} ({r['from_m']}-{r['to_m']} m): you {r['you_kw']} kW, it {r['ref_kw']} kW"
                for r in rows) + ". Whether the battery lasts is not checked here: look at your battery level too.")
        return {"answer": text, "ref": ref_name, "exits": rows,
                "note": "Zones are counted in braking order, not the game's turn numbers."}
