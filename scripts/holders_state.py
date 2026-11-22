"""Holder state: every address's balance at the end of a UTC day, plus its first and last top-up day, its last
outflow day, and its daily net movement over the last 40 days. Created once per token from a full transfer scan
(holders_scan.py), then rolled forward each day from the flows engine's per-day `moves`, so the daily job never
rereads a token's history.

Usage:
  holders_state.py init <scan.json.gz> <edges.json> <state.json.gz>
  holders_state.py roll <state.json.gz> <flows_cache_dir>      apply every cached flows day after the state's day
"""
import datetime as dt, gzip, json, os, sys

KEEP_DAYS = 40


def _day_of_block(b, edges_sorted):
    """UTC date of block b from the first-block-of-day table; older blocks are estimated at the table's rate."""
    if b is None:
        return None
    first_d, first_b = edges_sorted[0]
    if b < first_b:
        last_d, last_b = edges_sorted[-1]
        per_day = (last_b - first_b) / max(1, (dt.date.fromisoformat(last_d) - dt.date.fromisoformat(first_d)).days)
        back = int((first_b - b) / per_day) + 1
        return (dt.date.fromisoformat(first_d) - dt.timedelta(days=back)).isoformat()
    lo = first_d
    for d, eb in edges_sorted:
        if eb <= b:
            lo = d
        else:
            break
    return lo


def init(scan_path, edges_path, out_path):
    with gzip.open(scan_path, "rt") as f:
        rec = json.load(f)
    edges = json.load(open(edges_path))
    es = sorted((d, v[1]) for d, v in edges.items())
    days, n = rec["days"], len(rec["days"])
    addrs = {}
    for a, (bal, fi, li, lo, daily) in rec["addrs"].items():
        after = int(daily.get(str(n), 0))
        dd = {days[int(k)]: int(v) for k, v in daily.items() if int(k) < n and int(v)}
        b = int(bal) - after
        ins = [d for d, v in dd.items() if v > 0]
        outs = [d for d, v in dd.items() if v < 0]
        last_in = max(ins) if ins else (_day_of_block(li, es) if li else None)
        last_out = max(outs) if outs else (_day_of_block(lo, es) if lo else None)
        if last_in and last_in > days[-1]:
            last_in = days[-1]
        if last_out and last_out > days[-1]:
            last_out = days[-1]
        if b == 0 and not dd:
            continue
        addrs[a] = [b, _day_of_block(fi, es) if fi else None, last_in, last_out, dd]
    state = {k: rec[k] for k in ("symbol", "token", "category", "decimals", "pools", "kinds", "closes", "supply", "transfers")}
    # balances that go negative mean the token changes balances without Transfer events (reflections, silent mints):
    # it can't be rebuilt from transfers, so the state is marked and left out of scoring
    state["rebuildable"] = rec["check"]["negatives"] == 0
    state.update({"asof": days[-1], "addrs": addrs})
    with gzip.open(out_path, "wt") as f:
        json.dump(state, f, separators=(",", ":"))
    return state


def apply_day(state, day, moves):
    for a, v in moves.items():
        v = int(v)
        if not v:
            continue
        x = state["addrs"].setdefault(a, [0, None, None, None, {}])
        x[0] += v
        x[4][day] = x[4].get(day, 0) + v
        if v > 0:
            x[2] = day
            x[1] = x[1] or day
        else:
            x[3] = day
    cut = (dt.date.fromisoformat(day) - dt.timedelta(days=KEEP_DAYS - 1)).isoformat()
    for a in list(state["addrs"]):
        x = state["addrs"][a]
        x[4] = {d: v for d, v in x[4].items() if d >= cut}
        if x[0] == 0 and not x[4]:
            del state["addrs"][a]
    state["asof"] = day


def roll(state_path, flows_dir):
    with gzip.open(state_path, "rt") as f:
        state = json.load(f)
    tdir = os.path.join(flows_dir, state["symbol"])
    days = sorted(fn[:10] for fn in os.listdir(tdir) if fn[:4].isdigit() and fn.endswith(".json.gz"))
    new = [d for d in days if d > state["asof"]]
    # only consecutive days: a gap would make balances wrong
    for d in new:
        if (dt.date.fromisoformat(d) - dt.date.fromisoformat(state["asof"])).days != 1:
            break
        with gzip.open(os.path.join(tdir, f"{d}.json.gz"), "rt") as f:
            day = json.load(f)
        if "moves" not in day:
            break
        apply_day(state, d, day["moves"])
        if day.get("price_close"):
            state["closes"][d] = day["price_close"]
    tmp = state_path + ".tmp"
    with gzip.open(tmp, "wt") as f:
        json.dump(state, f, separators=(",", ":"))
    os.replace(tmp, state_path)
    return state


if __name__ == "__main__":
    if sys.argv[1] == "init":
        s = init(sys.argv[2], sys.argv[3], sys.argv[4]); print(s["symbol"], "state through", s["asof"], len(s["addrs"]), "addresses")
    elif sys.argv[1] == "roll":
        s = roll(sys.argv[2], sys.argv[3]); print(s["symbol"], "state through", s["asof"])
    else:
        raise SystemExit(__doc__)
