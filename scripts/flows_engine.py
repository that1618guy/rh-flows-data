"""Flows engine, pipeline version: who bought and sold each tracked Robinhood Chain token, per UTC day, cached.

Each run, for every token in the universe:
  1. computes any missing full UTC days (up to --max-new per run, newest first), one cache file per day:
     every swap in the token's main pools (v2/v3-style pools and the Uniswap v4 pool manager), attributed to the
     addresses whose token balance changed in that transaction (routers net to zero), valued at that hour's price,
     plus each trader's net token movement that day (all transfers) and the day's mint/burn;
  2. refreshes a head snapshot: balances of every wallet that traded in the cached window, pinned to one block, and
     each one's transfers after the latest cached day, so window start and end holdings can be rebuilt.

Cache layout (data/flows_cache/):
  codes.json                  address -> wallet kind (eoa, 7702, cNN = contract with NN bytes of code)
  edges.json                  UTC date -> [timestamp, first block of that day]
  <SYM>/meta.json             token facts (address, category, decimals, pricing pool, pools)
  <SYM>/<YYYY-MM-DD>.json.gz  one per day: prices, swaps, mint, burn, rows {wallet: [bought_usd, sold_usd, trades, net_raw]},
                              moves {address: net_raw} for every address whose balance changed that day
  <SYM>/head.json.gz          block, day, bal {wallet: raw}, after {wallet: raw}

Usage: flows_engine.py --universe data/flows_universe.json --cache data/flows_cache [--days 30] [--max-new 4]
                       [--budget-min 40] [--workers 3] [--asof YYYY-MM-DD] [--only SYM,SYM]

--budget-min caps the backfill, not the daily work: every token always gets its newest missing day (normally
yesterday) and a fresh head snapshot; older missing days are only started while the run is inside the budget,
and the rest wait for the next run. Missing days anywhere in the window are retried every run, newest first.
"""
import argparse, bisect, collections, datetime as dt, gzip, json, os, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rh

GT = "https://api.geckoterminal.com/api/v2"
MAJORS = {"WETH", "USDG", "USDC", "USDT", "ETH", "MMETH", "CBBTC"}
GT_GAP = float(os.environ.get("GT_GAP", "2.4"))     # seconds between GeckoTerminal calls, across all threads (~25/min)
MIN_POOL_LIQ = 5000                                    # main pools: $5K+ of liquidity

_gt_lock = threading.Lock()
_gt_next = [0.0]
DEADLINE = [0.0]                                       # unix time after which no backfill day is started (0 = none)


def gt(url):
    """GeckoTerminal GET, rate-limited across threads; a 429 pauses every thread for a minute."""
    err = None
    for k in range(8):
        with _gt_lock:
            wait = _gt_next[0] - time.time()
            if wait > 0:
                time.sleep(wait)
            _gt_next[0] = time.time() + GT_GAP
        try:
            req = urllib.request.Request(url, headers={"accept": "application/json;version=20230302", "user-agent": "sob-flows"})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            err = e
            if e.code == 429:
                with _gt_lock:
                    _gt_next[0] = max(_gt_next[0], time.time() + 61)
            else:
                time.sleep(6 * (k + 1))
        except Exception as e:  # network hiccup
            err = e
            time.sleep(6 * (k + 1))
    raise err


def utc_midnight(d):
    D = dt.date.fromisoformat(d)
    return int(dt.datetime(D.year, D.month, D.day, tzinfo=dt.timezone.utc).timestamp())


def day_edges(days, cache):
    """First block of each UTC day (and of the day after the last), cached forever in edges.json."""
    path = os.path.join(cache, "edges.json")
    edges = json.load(open(path)) if os.path.exists(path) else {}
    need = sorted(set(days) | {(dt.date.fromisoformat(max(days)) + dt.timedelta(days=1)).isoformat()})
    missing = [d for d in need if d not in edges]
    if missing:
        h = rh.head(); h_ts = rh.block_ts(h)
        rate = 200000 / max(1, h_ts - rh.block_ts(h - 200000))
        for d in missing:
            ts = utc_midnight(d)
            if ts > h_ts:
                raise SystemExit(f"day {d} has not started on chain yet")
            guess = int(h - (h_ts - ts) * rate)
            lo, hi = max(1, guess - 300000), min(h, guess + 300000)
            while rh.block_ts(lo) > ts:
                lo = max(1, lo - 600000)
            while hi < h and rh.block_ts(hi) < ts:
                hi = min(h, hi + 600000)
            edges[d] = [ts, rh.block_at(ts, lo=lo, hi=hi)]
        json.dump(edges, open(path, "w"), indent=0, sort_keys=True)
    return edges


def bounds_for(day, edges):
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    return edges[day][1], edges[nxt][1] - 1, edges[day][0], edges[nxt][0]   # b0, b1, t0, t1


def token_pools(token):
    allp = []
    for page in (1, 2):
        d = gt(f"{GT}/networks/robinhood/tokens/{token}/pools?page={page}&include=base_token,quote_token,dex")
        inc = {x["id"]: x["attributes"] for x in d.get("included", [])}
        for p in d.get("data", []):
            a = p["attributes"]; r = p["relationships"]
            base = inc.get(r["base_token"]["data"]["id"], {}); quote = inc.get(r["quote_token"]["data"]["id"], {})
            mine_is_base = (base.get("address") or "").lower() == token
            other = quote if mine_is_base else base
            allp.append({"id": a["address"].lower(), "dex": r["dex"]["data"]["id"], "name": a["name"],
                         "liq": float(a["reserve_in_usd"] or 0), "vol24": float((a.get("volume_usd") or {}).get("h24") or 0),
                         "side": "base" if mine_is_base else "quote", "other": (other.get("symbol") or "").upper()})
        if len(d.get("data", [])) < 20:
            break
    return allp


def prepare(token):
    token = token.lower()
    allp = token_pools(token)
    main = [p for p in allp if p["liq"] >= MIN_POOL_LIQ]
    if not main:
        return None
    ranked = sorted(main, key=lambda p: (p["other"] not in MAJORS, -p["vol24"], -p["liq"]))
    return {"token": token, "decimals": rh.decimals(token), "pricing": ranked[0], "ranked": ranked[:4], "main": main,
            "v2v3": [p["id"] for p in main if len(p["id"]) == 42],
            "v4": [p["id"] for p in main if len(p["id"]) == 66],
            "pool_addrs": {p["id"] for p in allp if len(p["id"]) == 42} | {rh.POOL_MANAGER, token, rh.ZERO, rh.DEAD}}


def hourly_prices(ctx, since_ts):
    """Hourly closes in USD from the most-traded pool that actually has candles (some v4 pools return none)."""
    hours = int((time.time() - since_ts) // 3600) + 30
    for p in ctx["ranked"]:
        oh = gt(f"{GT}/networks/robinhood/pools/{p['id']}/ohlcv/hour?aggregate=1&limit={min(1000, hours)}&currency=usd&token={p['side']}")
        px = {int(c[0]) // 3600 * 3600: float(c[4]) for c in oh["data"]["attributes"]["ohlcv_list"]}
        if len(px) >= 12:
            ctx["pricing"] = p
            return px
    return {}


def price_at(px, ts):
    hr = ts // 3600 * 3600
    for k in range(72):
        if hr - 3600 * k in px:
            return px[hr - 3600 * k]
    return None


def compute_day(ctx, px, b0, b1, t0, t1):
    """One UTC day: swaps attributed to wallets, each trader's net movement that day, mint and burn."""
    token, dec, pool_addrs = ctx["token"], 10 ** ctx["decimals"], ctx["pool_addrs"]
    swaps = {}
    if ctx["v2v3"]:
        for ch in rh.iter_logs(ctx["v2v3"], [[rh.SWAP_V3, rh.SWAP_V2]], b0, b1):
            for l in ch:
                swaps[l["transactionHash"]] = int(l["blockNumber"], 16)
    if ctx["v4"]:
        for ch in rh.iter_logs(rh.POOL_MANAGER, [rh.SWAP_V4, ctx["v4"]], b0, b1):
            for l in ch:
                swaps[l["transactionHash"]] = int(l["blockNumber"], 16)
    rows = collections.defaultdict(lambda: [0.0, 0.0, 0, 0, 0])      # usd bought, usd sold, trades, raw bought, raw sold
    net = collections.Counter(); mint = burn = 0; nsw = 0
    cur_tx, cur = None, collections.defaultdict(int)

    def flush(txh, deltas):
        nonlocal nsw
        bn = swaps[txh]
        ts = t0 + int((bn - b0) * (t1 - t0) / max(1, b1 - b0)); p = price_at(px, ts) or 0
        nsw += 1
        for a, dlt in deltas.items():
            if a in pool_addrs or dlt == 0:
                continue
            usd = abs(dlt) / dec * p; r = rows[a]
            if dlt > 0:
                r[0] += usd; r[3] += dlt
            else:
                r[1] += usd; r[4] -= dlt
            r[2] += 1

    for ch in rh.iter_logs(token, [rh.TRANSFER], b0, b1):
        for l in ch:
            amt = int(l["data"], 16)
            fr, to = rh.topic_addr(l["topics"][1]), rh.topic_addr(l["topics"][2])
            net[fr] -= amt; net[to] += amt
            if fr == rh.ZERO:
                mint += amt
            if to in (rh.ZERO, rh.DEAD):
                burn += amt
            txh = l["transactionHash"]
            if txh != cur_tx:
                if cur_tx in swaps:
                    flush(cur_tx, cur)
                cur_tx, cur = txh, collections.defaultdict(int)
            if txh in swaps:
                cur[fr] -= amt; cur[to] += amt
    if cur_tx in swaps:
        flush(cur_tx, cur)
    return {"price_open": price_at(px, t0), "price_close": price_at(px, t1 - 1), "swaps": nsw,
            "mint": str(mint), "burn": str(burn),
            "rows": {a: [round(r[0], 2), round(r[1], 2), r[2], str(net.get(a, 0)), str(r[3]), str(r[4])] for a, r in rows.items()},
            "moves": {a: str(v) for a, v in net.items() if v}}


def compute_head(ctx, last_b1, traders, codes):
    """Balances of every recent trader pinned to one block, plus their transfers after the latest cached day."""
    hb = rh.head()
    try:
        bal = rh.balances(ctx["token"], traders, block=hb) if traders else {}
    except Exception:
        hb = rh.head(); bal = rh.balances(ctx["token"], traders, block="latest") if traders else {}
    tset = set(traders); after = collections.Counter()
    for ch in rh.iter_logs(ctx["token"], [rh.TRANSFER], last_b1 + 1, hb):
        for l in ch:
            amt = int(l["data"], 16)
            fr, to = rh.topic_addr(l["topics"][1]), rh.topic_addr(l["topics"][2])
            if fr in tset: after[fr] -= amt
            if to in tset: after[to] += amt
    new = [a for a in traders if a not in codes]
    for a, c in zip(new, rh.batch("eth_getCode", [[a, "latest"] for a in new]) if new else []):
        c = c or "0x"
        codes[a] = "eoa" if len(c) <= 2 else ("7702" if c.startswith("0xef0100") else f"c{len(c) // 2}")
    return {"block": hb, "bal": {a: str(v) for a, v in bal.items() if v}, "after": {a: str(v) for a, v in after.items() if v}}


def run_token(t, days, edges, cache, max_new, codes):
    sym = t["symbol"]; tdir = os.path.join(cache, sym); os.makedirs(tdir, exist_ok=True)
    st = time.time()
    ctx = prepare(t["address"])
    if not ctx:
        return f"{sym:9s} skipped: no pool with ${MIN_POOL_LIQ:,}+ liquidity"
    have = {f[:10] for f in os.listdir(tdir) if f.endswith(".json.gz") and f[:4].isdigit()}
    missing = sorted([d for d in days if d not in have], reverse=True)[:max_new]   # newest first
    done = []
    if missing:
        px = hourly_prices(ctx, edges[min(missing)][0])
        for d in missing:
            if d != days[-1] and DEADLINE[0] and time.time() > DEADLINE[0]:
                break                          # out of budget: older days wait for the next run
            b0, b1, t0, t1 = bounds_for(d, edges)
            rec = compute_day(ctx, px, b0, b1, t0, t1)
            tmp = os.path.join(tdir, f".{d}.tmp")
            with gzip.open(tmp, "wt") as f:
                json.dump(rec, f, separators=(",", ":"))
            os.replace(tmp, os.path.join(tdir, f"{d}.json.gz"))
            done.append(d)
    cached = sorted(f[:10] for f in os.listdir(tdir) if f.endswith(".json.gz") and f[:4].isdigit())
    window = [d for d in cached if d in set(days)]
    if not window:
        return f"{sym:9s} no cached days"
    traders = set()
    for d in window:
        with gzip.open(os.path.join(tdir, f"{d}.json.gz"), "rt") as f:
            traders |= set(json.load(f)["rows"])
    last = window[-1]
    head = compute_head(ctx, bounds_for(last, edges)[1], sorted(traders), codes)
    head.update({"day": last, "kinds": {a: codes.get(a, "eoa") for a in traders}})
    with gzip.open(os.path.join(tdir, ".head.tmp"), "wt") as f:
        json.dump(head, f, separators=(",", ":"))
    os.replace(os.path.join(tdir, ".head.tmp"), os.path.join(tdir, "head.json.gz"))
    p = ctx["pricing"]; mpath = os.path.join(tdir, "meta.json")
    pricing = f"{p['name']} · {p['dex']}"
    if not done and os.path.exists(mpath):        # no prices fetched this run: keep the pool the cached days used
        pricing = json.load(open(mpath)).get("pricing_pool") or pricing
    json.dump({"symbol": sym, "token": ctx["token"], "category": t["category"], "name": t.get("name") or sym,
               "decimals": ctx["decimals"], "pricing_pool": pricing, "main_pools": len(ctx["main"]),
               "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
              open(os.path.join(tdir, "meta.json"), "w"), indent=1)
    left = len([d for d in days if d not in set(cached)])
    return (f"{sym:9s} ok: {len(done)} new day(s), {len(window)} of {len(days)} cached"
            f"{f', {left} still to backfill' if left else ''}, {len(traders):,} traders, {time.time() - st:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True); ap.add_argument("--cache", required=True)
    ap.add_argument("--days", type=int, default=30); ap.add_argument("--max-new", type=int, default=4)
    ap.add_argument("--budget-min", type=float, default=0)
    ap.add_argument("--workers", type=int, default=3); ap.add_argument("--asof"); ap.add_argument("--only")
    a = ap.parse_args()
    t_start = time.time()
    DEADLINE[0] = t_start + a.budget_min * 60 if a.budget_min else 0
    os.makedirs(a.cache, exist_ok=True)
    asof = dt.date.fromisoformat(a.asof) if a.asof else dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    days = [(asof - dt.timedelta(days=i)).isoformat() for i in range(a.days)][::-1]
    universe = json.load(open(a.universe))
    if a.only:
        keep = set(a.only.split(",")); universe = [t for t in universe if t["symbol"] in keep]
    edges = day_edges(days, a.cache)
    cpath = os.path.join(a.cache, "codes.json")
    codes = json.load(open(cpath)) if os.path.exists(cpath) else {}
    print(f"flows: {len(universe)} tokens, days {days[0]}..{days[-1]}, up to {a.max_new} new day(s) per token"
          f"{f', backfill budget {a.budget_min:g} min' if a.budget_min else ''}", flush=True)
    failed = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(run_token, t, days, edges, a.cache, a.max_new, codes): t["symbol"] for t in universe}
        for f in as_completed(futs):
            try:
                print(f.result(), flush=True)
            except Exception as e:
                failed += 1
                print(f"{futs[f]:9s} FAILED: {str(e)[:200]}", flush=True)
    json.dump(codes, open(cpath, "w"))
    # prune day files older than the window (plus a few days of slack)
    oldest = (asof - dt.timedelta(days=a.days + 4)).isoformat()
    for sym in os.listdir(a.cache):
        tdir = os.path.join(a.cache, sym)
        if os.path.isdir(tdir):
            for fn in os.listdir(tdir):
                if fn[:4].isdigit() and fn[:10] < oldest:
                    os.remove(os.path.join(tdir, fn))
    print(f"flows: done in {(time.time() - t_start) / 60:.1f} min, {failed} failed", flush=True)
    sys.exit(1 if failed > len(universe) // 3 else 0)


if __name__ == "__main__":
    main()
