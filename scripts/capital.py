"""Capital on Robinhood Chain: how much money sits on the chain and how much arrived or left each day.

  stablecoins  mints less burns of each stablecoin on Robinhood Chain, read from the chain's own logs (exact)
  ETH          change in the ETH held by the chain's canonical bridge on Ethereum (DefiLlama, protocol
               robinhood-chain-bridge, token units), valued at that day's closing ETH price
  net flow     stablecoins + ETH, per UTC day

Every ETH on the chain is backed by ETH in that bridge, so its balance is the chain's ETH whichever bridge a
user took. Stablecoins minted on the chain are counted at $1.

Usage: capital.py <cache_dir> <out.json> [--full]   (cache_dir is data/flows_cache; state lives in <cache_dir>/capital)

Each run only reads the blocks since the last one. With no saved state it would have to read the chain's whole
history (about an hour), so it stops instead unless --full is given: restore the capital-seed release first.
"""
import bisect, datetime as dt, json, os, sys, threading, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rh

STABLES = {                                    # symbol: (address, decimals)
    "USDG": ("0x5fc5360d0400a0fd4f2af552add042d716f1d168", 6),
    "USDe": ("0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34", 18),
    "syrupUSDG": ("0x40858070814a57fdf33a613ae84fe0a8b4a874f7", 6),
}
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
ETH_POOL = "0x52e65b17fb6e5ba00ed806f37afcd2daa50271ca"          # USDG / WETH 0.01%, the deepest ETH pool
LLAMA = "https://api.llama.fi"
GT = "https://api.geckoterminal.com/api/v2/networks/robinhood"
Z = "0x" + "0" * 64
STEP = 800_000
SUPPLY_TOL = 0.001                             # rebuilt supply must match totalSupply within 0.1%


def get(url, tries=6):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "sob-capital"})
            return json.load(urllib.request.urlopen(req, timeout=90))
        except urllib.error.HTTPError as e:
            if e.code == 404 or k == tries - 1:
                raise
            time.sleep(20 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(10 * (k + 1))


def day_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def day_edges(cache, first_day, last_day):
    """{day: first block of that UTC day}, for first_day..last_day, found by bisection in batched calls and
    cached forever. Logs on this chain carry no usable timestamp, so events are dated by block."""
    path = os.path.join(cache, "edges.json")
    edges = json.load(open(path)) if os.path.exists(path) else {}
    need, d = [], first_day
    while d <= last_day:
        if d not in edges:
            need.append(d)
        d = (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
    if need:
        head = rh.head()
        ts = {d: int(dt.datetime.fromisoformat(d + "T00:00:00+00:00").timestamp()) for d in need}
        lo, hi = {d: 1 for d in need}, {d: head for d in need}
        while True:
            open_ = [d for d in need if lo[d] < hi[d]]
            if not open_:
                break
            mids = {d: (lo[d] + hi[d]) // 2 for d in open_}
            res = rh.batch("eth_getBlockByNumber", [[hex(mids[d]), False] for d in open_])
            for d, r in zip(open_, res):
                if not r:
                    raise RuntimeError(f"capital: no block {mids[d]}")
                if int(r["timestamp"], 16) < ts[d]:
                    lo[d] = mids[d] + 1
                else:
                    hi[d] = mids[d]
        for d in need:
            edges[d] = lo[d]
        tmp = path + ".tmp"
        json.dump(edges, open(tmp, "w"), sort_keys=True); os.replace(tmp, path)
    return edges


def scan_token(sym, cache, head):
    """Add this token's mints and burns from the last scanned block to head, saving as it goes.
    events: [block, amount] in token units, negative for a burn."""
    addr, dec = STABLES[sym]
    path = os.path.join(cache, f"{sym}.json")
    st = json.load(open(path)) if os.path.exists(path) else {"through": 0, "events": []}
    a = st["through"] + 1
    while a <= head:
        b = min(head, a + STEP - 1)
        for sign, topics in ((1, [rh.TRANSFER, Z]), (-1, [rh.TRANSFER, None, Z])):
            for lg in rh.logs(addr, topics, a, b, step=STEP):
                amt = int(lg["data"], 16) / 10 ** dec
                if amt:
                    st["events"].append([int(lg["blockNumber"], 16), round(sign * amt, 6)])
        st["through"] = b
        tmp = path + ".tmp"
        json.dump(st, open(tmp, "w")); os.replace(tmp, path)
        a = b + 1
    supply = int(rh.rpc("eth_call", [{"to": addr, "data": "0x18160ddd"}, hex(head)])["result"], 16) / 10 ** dec
    built = sum(e[1] for e in st["events"])
    st["supply"] = supply; st["built"] = built
    st["ok"] = abs(built - supply) <= SUPPLY_TOL * max(supply, 1)
    print(f"capital: {sym:10s} supply {supply:>16,.0f} rebuilt {built:>16,.0f} {'ok' if st['ok'] else 'MISMATCH'} ({len(st['events'])} mints and burns)", flush=True)
    return st


def scan(cache, full=False):
    os.makedirs(cache, exist_ok=True)
    missing = [s for s in STABLES if not os.path.exists(os.path.join(cache, f"{s}.json"))]
    if missing and not full:
        raise SystemExit(f"capital: no saved state for {', '.join(missing)}. Restore the capital-seed release into {cache}, "
                         "or run once with --full (about an hour).")
    head = rh.head() - 20
    out, th = {}, []
    for sym in STABLES:
        t = threading.Thread(target=lambda s=sym: out.__setitem__(s, scan_token(s, cache, head)))
        t.start(); th.append(t)
    for t in th:
        t.join()
    if len(out) != len(STABLES):
        raise RuntimeError("capital: a stablecoin scan failed")
    return out


def by_day(events, edges):
    """{day: [minted, burned, mints, burns]} from [block, amount] events and the first-block-of-day table."""
    ds = sorted(edges)
    blocks = [edges[d] for d in ds]
    out = {}
    for blk, amt in events:
        i = bisect.bisect_right(blocks, blk) - 1
        if i < 0:
            continue
        x = out.setdefault(ds[i], [0.0, 0.0, 0, 0])
        if amt > 0:
            x[0] += amt; x[2] += 1
        else:
            x[1] -= amt; x[3] += 1
    return out


def eth_series():
    """{day: ETH in the bridge at 00:00 UTC that day} and {day: (open, close)} ETH price."""
    p = get(f"{LLAMA}/protocol/robinhood-chain-bridge")
    units = {}
    for x in p["chainTvls"]["Ethereum"]["tokens"]:
        if x["date"] % 86400 == 0:
            units[day_of(x["date"])] = float(x["tokens"].get("WETH") or 0)
    o = get(f"{GT}/pools/{ETH_POOL}/ohlcv/day?limit=1000&currency=usd&token={WETH}")
    px = {day_of(c[0]): (c[1], c[4]) for c in o["data"]["attributes"]["ohlcv_list"]}
    return units, px


def chain_series():
    """Daily TVL, DEX volume and app fees for Robinhood Chain from DefiLlama."""
    q = "excludeTotalDataChartBreakdown=true"
    tvl = {day_of(x["date"]): x["tvl"] for x in get(f"{LLAMA}/v2/historicalChainTvl/Robinhood")}
    vol = {day_of(t): v for t, v in get(f"{LLAMA}/overview/dexs/Robinhood?{q}")["totalDataChart"]}
    fees = {day_of(t): v for t, v in get(f"{LLAMA}/overview/fees/Robinhood?{q}&dataType=dailyFees")["totalDataChart"]}
    return tvl, vol, fees


def build(cache_dir, full=False):
    cache = os.path.join(cache_dir, "capital")
    st = scan(cache, full)
    units, px = eth_series()
    tvl, vol, fees = chain_series()
    today = day_of(time.time())
    nxt = lambda d: (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
    first = max(min(units), min(px))                     # the series starts once the bridge and the ETH price both do
    edges = day_edges(cache, first, today)
    daily = {s: by_day(st[s]["events"], edges) for s in STABLES}
    start_block = edges[first]
    days, d = [], first
    while d < today:
        days.append(d); d = nxt(d)
    stock = {s: sum(e[1] for e in st[s]["events"] if e[0] < start_block) for s in STABLES}   # minted before day one
    # A day DefiLlama or GeckoTerminal skipped takes the last value before it, so the bridge's change over a gap
    # lands on the day the next snapshot arrives instead of being lost.
    ukeys, pkeys = sorted(units), sorted(px)
    def u_at(d):
        i = bisect.bisect_right(ukeys, d) - 1
        return units[ukeys[i]] if i >= 0 else None
    def p_at(d):
        i = bisect.bisect_right(pkeys, d) - 1
        return px[pkeys[i]] if i >= 0 else None
    rows = []
    for d in days:
        r = {"d": d, "st": {}}
        for s in STABLES:
            x = daily[s].get(d)
            net = x[0] - x[1] if x else 0.0
            stock[s] += net
            r["st"][s] = round(net)
        r["stables"] = sum(r["st"].values())
        r["stables_stock"] = round(sum(stock.values()))
        r["stock"] = {s: round(v) for s, v in stock.items()}
        u0 = u_at(d)                                             # bridge ETH at the start and end of the day
        u1 = u_at(nxt(d)) if nxt(d) <= ukeys[-1] else None       # the end-of-day snapshot may not be out yet
        p = p_at(d)
        r["eth_units"] = round(u1, 2) if u1 is not None else None
        r["eth_px"] = round(p[1], 2) if p else None
        r["eth_d"] = round(u1 - u0, 2) if u0 is not None and u1 is not None else None
        r["eth"] = round(r["eth_d"] * p[1]) if r["eth_d"] is not None and p else None
        r["net"] = r["stables"] + (r["eth"] or 0)
        r["tvl"] = round(tvl[nxt(d)]) if tvl.get(nxt(d)) else None      # DefiLlama stamps the day's close at next 00:00
        r["vol"] = round(vol[d]) if vol.get(d) is not None else None
        r["fees"] = round(fees[d]) if fees.get(d) is not None else None
        rows.append(r)
    while rows and rows[-1]["eth_d"] is None:                    # the bridge snapshot for the last day isn't out yet
        rows.pop()
    return {"asof": rows[-1]["d"], "rows": rows,
            "checks": {s: {"ok": st[s]["ok"], "supply": round(st[s]["supply"]), "rebuilt": round(st[s]["built"])} for s in STABLES}}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    m = build(sys.argv[1], "--full" in sys.argv)
    bad = [s for s, c in m["checks"].items() if not c["ok"]]
    if bad:                                    # rebuilt supply no longer matches the chain: don't publish a wrong series
        raise SystemExit(f"capital: rebuilt supply doesn't match totalSupply for {', '.join(bad)}; kept the previous file")
    tmp = sys.argv[2] + ".tmp"
    json.dump(m, open(tmp, "w"), separators=(",", ":")); os.replace(tmp, sys.argv[2])
    last7 = m["rows"][-7:]
    print(f"capital: through {m['asof']}, {len(m['rows'])} days; 7D net ${sum(r['net'] for r in last7)/1e6:+.1f}M "
          f"(stables ${sum(r['stables'] for r in last7)/1e6:+.1f}M, ETH ${sum(r['eth'] or 0 for r in last7)/1e6:+.1f}M)")
