"""Holder scan: every Transfer of a token, from a few days before its first pool to a pinned head block, folded into
per-address balances, first and last acquisition, last outflow, and daily net movement over the last --days UTC days.
Also stores the token's daily closes and the kind (wallet or contract) of every holder worth $1+. Feeds holders_model.

Usage: holders_scan.py --universe U --out data/holders [--codes flows_cache/codes.json] [--days 40] [--only SYM,..]
Output: <out>/<SYM>.json.gz, <out>/edges.json (first block of each UTC day)
"""
import argparse, bisect, collections, datetime as dt, gzip, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rh
import flows_engine as FE

SUPPLY_SEL = "0x18160ddd"
_codes_lock = threading.Lock()


def pools_with_dates(token):
    out = []
    for page in (1, 2, 3):
        d = FE.gt(f"{FE.GT}/networks/robinhood/tokens/{token}/pools?page={page}")
        for p in d.get("data", []):
            a = p["attributes"]
            ts = None
            if a.get("pool_created_at"):
                ts = int(dt.datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00")).timestamp())
            out.append({"id": a["address"].lower(), "created": ts, "liq": float(a["reserve_in_usd"] or 0)})
        if len(d.get("data", [])) < 20:
            break
    return out


def block_before(ts):
    """A block at or just before unix time ts."""
    h = rh.head(); h_ts = rh.block_ts(h)
    if ts >= h_ts:
        return h
    rate = 200000 / max(1, h_ts - rh.block_ts(h - 200000))
    guess = int(h - (h_ts - ts) * rate)
    lo, hi = max(1, guess - 300000), min(h, guess + 300000)
    while lo > 1 and rh.block_ts(lo) > ts:
        lo = max(1, lo - 2000000)
    while hi < h and rh.block_ts(hi) < ts:
        hi = min(h, hi + 2000000)
    return max(1, rh.block_at(ts, lo=lo, hi=hi) - 1)


def daily_closes(ctx):
    for p in ctx["ranked"]:
        oh = FE.gt(f"{FE.GT}/networks/robinhood/pools/{p['id']}/ohlcv/day?aggregate=1&limit=400&currency=usd&token={p['side']}")
        rows = oh["data"]["attributes"]["ohlcv_list"]
        if len(rows) >= 3:
            return {dt.datetime.fromtimestamp(int(c[0]), dt.timezone.utc).date().isoformat(): float(c[4]) for c in rows}, p
    return {}, None


def kinds_for(addrs, codes):
    """eoa / 7702 / cNN for each address, reusing known kinds; new ones are looked up in batches."""
    todo = [a for a in addrs if a not in codes]
    for i in range(0, len(todo), 400):
        chunk = todo[i:i + 400]
        res = rh.batch("eth_getCode", [[a, "latest"] for a in chunk], size=50)
        with _codes_lock:
            for a, c in zip(chunk, res):
                c = c or "0x"
                codes[a] = "eoa" if c == "0x" else "7702" if c.startswith("0xef0100") else f"c{len(c) // 2}"
    return {a: codes.get(a, "eoa") for a in addrs}


def scan(t, edges, day_list, codes, out_dir):
    st = time.time()
    sym, token = t["symbol"], t["address"].lower()
    ctx = FE.prepare(token)
    if not ctx:
        return f"{sym:9s} skipped: no pool with $5K+ liquidity"
    pools = pools_with_dates(token)
    start = 1                                # from the chain's first block: tokens can be minted long before their first pool
    hb = rh.head()
    try:                                     # read supply now: the node keeps state for recent blocks only
        supply = int((rh.rpc("eth_call", [{"to": token, "data": SUPPLY_SEL}, hex(hb)]) or {}).get("result") or "0x0", 16)
    except Exception:
        supply = 0
    edge_blocks = [edges[d][1] for d in day_list]                  # first block of each window day
    w0 = edge_blocks[0]
    after_b = edges[(dt.date.fromisoformat(day_list[-1]) + dt.timedelta(days=1)).isoformat()][1]
    bal = collections.defaultdict(int)
    first_in, last_in, last_out = {}, {}, {}
    daily = collections.defaultdict(lambda: collections.defaultdict(int))
    n = 0
    for chunk in rh.iter_logs(token, [rh.TRANSFER], start, hb):
        for lg in chunk:
            if len(lg["topics"]) < 3:
                continue
            v = int(lg["data"], 16) if lg["data"] not in ("0x", "") else 0
            if not v:
                continue
            frm, to = rh.topic_addr(lg["topics"][1]), rh.topic_addr(lg["topics"][2])
            b = int(lg["blockNumber"], 16)
            n += 1
            bal[frm] -= v; bal[to] += v
            last_out[frm] = b; last_in[to] = b
            if to not in first_in:
                first_in[to] = b
            if b >= w0:
                di = len(day_list) if b >= after_b else bisect.bisect_right(edge_blocks, b) - 1
                daily[frm][di] -= v; daily[to][di] += v
    neg = [a for a, v in bal.items() if v < 0 and a != rh.ZERO]
    held = sum(v for a, v in bal.items() if v > 0 and a != rh.ZERO)
    try:
        closes, pp = daily_closes(ctx)
    except Exception:
        closes, pp = {}, None
    px = closes.get(day_list[-1]) or (list(closes.values())[-1] if closes else 0)
    dec = 10 ** ctx["decimals"]
    worth = [a for a, v in bal.items() if v > 0 and a not in (rh.ZERO, rh.DEAD) and v / dec * px >= 1.0]
    try:
        kinds = kinds_for(worth, codes)
    except Exception:
        kinds = {x: codes.get(x, "eoa") for x in worth}
    keep = {a for a, v in bal.items() if v > 0} | set(daily)
    rec = {"symbol": sym, "token": token, "category": t["category"], "decimals": ctx["decimals"], "head_block": hb,
           "start_block": start, "supply": supply, "days": day_list, "closes": closes,
           "pricing_pool": f"{pp['name']} · {pp['dex']}" if pp else None,
           "pools": sorted({p["id"] for p in pools} | {rh.POOL_MANAGER}),
           "addrs": {a: [bal[a], first_in.get(a), last_in.get(a), last_out.get(a), dict(daily[a]) if a in daily else {}]
                     for a in keep},
           "kinds": kinds, "transfers": n,
           "check": {"held": held, "supply": supply, "negatives": len(neg)}}
    tmp = os.path.join(out_dir, f".{sym}.tmp")
    with gzip.open(tmp, "wt") as f:
        json.dump(rec, f, separators=(",", ":"))
    os.replace(tmp, os.path.join(out_dir, f"{sym}.json.gz"))
    gap = (held - supply) / supply * 100 if supply else 0
    return (f"{sym:9s} ok: {n:,} transfers, {sum(1 for v in bal.values() if v > 0):,} holders, "
            f"held vs supply {gap:+.3f}%, {len(neg)} negative, {time.time() - st:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--codes"); ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--asof"); ap.add_argument("--only"); ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    asof = a.asof or (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
    A = dt.date.fromisoformat(asof)
    day_list = [(A - dt.timedelta(days=i)).isoformat() for i in range(a.days)][::-1]
    edges = FE.day_edges(day_list, a.out)
    codes = json.load(open(a.codes)) if a.codes and os.path.exists(a.codes) else {}
    uni = json.load(open(a.universe))
    if a.only:
        want = a.only.split(",")
        uni = sorted([t for t in uni if t["symbol"] in want], key=lambda t: want.index(t["symbol"]))
    print(f"holders: {len(uni)} tokens, window {day_list[0]}..{day_list[-1]}", flush=True)
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(scan, t, edges, day_list, codes, a.out): t["symbol"] for t in uni}
        for f in as_completed(futs):
            try:
                print(f.result(), flush=True)
            except Exception as e:
                print(f"{futs[f]:9s} FAILED: {type(e).__name__}: {e}", flush=True)
    json.dump(codes, open(os.path.join(a.out, "codes.json"), "w"))


if __name__ == "__main__":
    main()
