#!/usr/bin/env python3
"""Stock tokens on Robinhood Chain, for the State of Robinhood Chain dashboard.

Runs in the daily refresh after the DefiLlama step and uses the same cutoff day (the last full
day in the DefiLlama data), so every number on the site runs through the same date.

Sources
  Robinhood Chain public RPC   the stock-token list (Robinhood's token factory), supply at the
                               cutoff, and the mint and burn events that rebuild supply day by day
  GeckoTerminal public API     prices, holder counts, pools and daily pool volume, including the
                               launchpads that pair new tokens with stock tokens (Long, Pons, Bankr)

Writes DATA_DIR/stocks.json and a daily snapshot in data/stocks/daily/. Everything slow to rebuild is cached
in data/stocks/ (supply by day, launches by day, prices and pool volume by day), so a new day only reads
the newest blocks and candles.

  python3 scripts/fetch_stocks.py                       # cutoff = last_full_day in DATA_DIR/rh_series.json
  python3 scripts/fetch_stocks.py --cutoff 2026-11-01   # pin a date
  python3 scripts/fetch_stocks.py --stage assemble      # rebuild stocks.json from the cache only
Standard library only, like the rest of the pipeline.
"""
import argparse, bisect, datetime as dt, json, os, re, sys, time, urllib.error, urllib.request

DATA = os.environ.get("DATA_DIR", "data/latest")
CACHE = os.environ.get("STOCKS_CACHE_DIR", "data/stocks")
RPC_URL = os.environ.get("RH_RPC", "https://rpc.mainnet.chain.robinhood.com")
GT_URL = "https://api.geckoterminal.com/api/v2"
NET = "robinhood"
UA = {"User-Agent": "stateofblocks-dashboard/1.0", "Accept": "application/json"}

FACTORY = "0x4783c67b63de2b358ac5951a7d41f47a38f3c046"   # Robinhood's stock-token factory
T_DEPLOYED = "0xd9b0c6a1c0de228715ad0fa09f3259686ee84f8cc675e03ef7e47a9cdafa76d6"  # (token, name, symbol)
T_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x" + "0" * 64
LAUNCHPADS = {"Long": ["long-xyz"], "Pons": ["pons-dot-family", "pons-v2", "pons-v2-dex"], "Bankr": ["bankr-robinhood"]}
# Launch events, read straight from each launchpad's contracts. Fields are counted from topic 1,
# then the data words. Long and Bankr launch through one shared factory, so its count covers both.
LAUNCH_EVENTS = {
    "Pons": {"address": "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e", "topic": "0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607",
              "token": 0, "quote": 3},
    "Long and Bankr": {"address": "0x4e3468951d49f2eea976ed0d6e75ffcb44a9a544", "topic": "0xb224da6575b2c2ffd42454faedb236f7dbe5f92a0c96bb99c0273dbe98464c7e",
                        "token": 1, "quote": 2},
}
HISTORY_DAYS, VOLUME_DAYS = 90, 30
SPAN_ONE, SPAN_MANY = 10_000_000, 100_000          # RPC log-range limits: one address / many addresses
GT_GAP = float(os.environ.get("GT_GAP", "2.1"))    # GeckoTerminal's public API allows ~30 calls a minute
GT_BUDGET = float(os.environ.get("GT_BUDGET", "2400"))   # the GeckoTerminal stage stops after 40 minutes, saves its place,
                                                          # and the next run finishes the rest
# 60 tokens hold ~98% of value; the 200 busiest pools carry ~98% of volume
PRICE_HISTORY_TOKENS, POOL_LIST_TOKENS, VOLUME_POOLS, HOLDERS_DAILY = 60, 40, 200, 20
STABLES = {"USDG", "USDC", "USDT", "USDC.E", "USDE", "DAI", "PYUSD", "FRXUSD", "USD1"}


def log(*a):
    print("[stocks]", *a, flush=True)


def load(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    if default is None:
        raise SystemExit(f"missing {path}: run the earlier stages first")
    return default


def save(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(path + ".tmp", path)


def midnight(d):
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())


# ---------------------------------------------------------------- Robinhood Chain RPC
class RPCError(Exception):
    pass


_rpc_t = [0.0]
_rpc_n = [0]


def _post(payload):
    req = urllib.request.Request(RPC_URL, data=json.dumps(payload).encode(),
                                 headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _passing(e):
    """A JSON-RPC error worth waiting out: a rate limit, or the RPC's own backend failing ("failed to call backend
    endpoint"). Both back off and retry; with 16 tries the waits add up to about 4 minutes, so an outage still fails."""
    m = str(e.get("message"))
    return e.get("code") == 429 or "Too Many" in m or "backend endpoint" in m


def rpc(method, params, tries=16):
    for k in range(tries):
        wait = 0.15 - (time.time() - _rpc_t[0])
        if wait > 0:
            time.sleep(wait)
        _rpc_t[0] = time.time()
        try:
            j = _post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        except Exception:
            time.sleep(min(30, 1 + 2 * k))
            continue
        if "error" in j:
            e = j["error"]
            if _passing(e):
                time.sleep(min(30, 1 + 2 * k))
                continue
            raise RPCError(str(e.get("message")))
        _rpc_n[0] += 1
        return j["result"]
    raise RuntimeError(f"RPC {method} kept failing")


def rpc_batch(calls, size=12, tries=16):
    out = []
    for i in range(0, len(calls), size):
        grp = calls[i:i + size]
        payload = [{"jsonrpc": "2.0", "id": n, "method": m, "params": p} for n, (m, p) in enumerate(grp)]
        for k in range(tries):
            wait = 0.4 - (time.time() - _rpc_t[0])
            if wait > 0:
                time.sleep(wait)
            _rpc_t[0] = time.time()
            try:
                j = _post(payload)
            except Exception:
                time.sleep(min(30, 1 + 2 * k))
                continue
            if isinstance(j, dict) or any("error" in x and _passing(x["error"]) for x in j):
                time.sleep(min(30, 1 + 2 * k))
                continue
            res = [None] * len(grp)
            for x in j:
                res[x["id"]] = x.get("result")
            out += res
            break
        else:
            raise RuntimeError("RPC batch kept failing")
    return out


def get_logs(flt, a, b, span):
    """eth_getLogs over a..b, split into the RPC's allowed range and halved when a chunk is too full."""
    out, todo = [], [(x, min(x + span - 1, b)) for x in range(a, b + 1, span)][::-1]
    while todo:
        lo, hi = todo.pop()
        try:
            out += rpc("eth_getLogs", [{**flt, "fromBlock": hex(lo), "toBlock": hex(hi)}])
        except RPCError as e:
            if hi > lo and ("exceeds limit" in str(e) or "allowed" in str(e)):
                mid = (lo + hi) // 2
                todo += [(mid + 1, hi), (lo, mid)]
                continue
            raise
        if _rpc_n[0] % 200 == 0:
            log(f"  logs: block {hi:,} of {b:,}, {len(out):,} events")
    return out


_ts = {}


def block_ts(n):
    if n not in _ts:
        _ts[n] = int(rpc("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)
    return _ts[n]


def first_block_at(t, lo, hi):
    """Smallest block in (lo, hi] with timestamp >= t, given block_ts(lo) < t <= block_ts(hi)."""
    secant = True
    while hi - lo > 1:
        tl, th = block_ts(lo), block_ts(hi)
        if secant and th > tl:
            g = min(max(lo + int((t - tl) * (hi - lo) / (th - tl)), lo + 1), hi - 1)
        else:
            g = (lo + hi) // 2
        before = hi - lo
        if block_ts(g) >= t:
            hi = g
        else:
            lo = g
        secant = (hi - lo) < before / 2 or not secant      # halve when the interpolation stalls
    return hi


def day_end_blocks(days, latest):
    path = os.path.join(CACHE, "day_blocks.json")
    cache = load(path, {})
    for d in days:
        k = d.isoformat()
        if k in cache:
            continue
        t = midnight(d + dt.timedelta(days=1))
        if block_ts(latest) < t:
            raise SystemExit(f"{k} hasn't closed on-chain yet")
        lo = cache.get((d - dt.timedelta(days=1)).isoformat(), 1)
        if block_ts(lo) >= t:
            lo = 1
        hi = min(latest, lo + 1_300_000)
        if block_ts(hi) < t:
            hi = latest
        cache[k] = first_block_at(t, lo, hi) - 1
    save(path, cache)
    return {d: cache[d.isoformat()] for d in days}


def load_tokens(upto):
    """Every token Robinhood's factory has deployed up to block `upto` (cached, scanned incrementally)."""
    path = os.path.join(CACHE, "tokens.json")
    c = load(path, {"scanned_to": -1, "tokens": []})
    if c["scanned_to"] < upto:
        for l in get_logs({"address": FACTORY, "topics": [T_DEPLOYED]}, c["scanned_to"] + 1, upto, SPAN_ONE):
            d = l["data"][2:]

            def s(off):
                n = int(d[off * 2:off * 2 + 64], 16)
                return bytes.fromhex(d[off * 2 + 64:off * 2 + 64 + n * 2]).decode("utf-8", "replace")
            c["tokens"].append({"address": ("0x" + d[24:64]).lower(), "name": s(int(d[64:128], 16)),
                                "symbol": s(int(d[128:192], 16)), "block": int(l["blockNumber"], 16)})
        c["scanned_to"] = upto
        save(path, c)
    return [t for t in c["tokens"] if t["block"] <= upto]


def supplies_now(addrs):
    for attempt in range(4):
        L = int(rpc("eth_blockNumber", []), 16)
        res = rpc_batch([("eth_call", [{"to": a, "data": "0x18160ddd"}, hex(L)]) for a in addrs])
        if all(isinstance(r, str) and len(r) > 2 for r in res):
            return L, [int(r, 16) for r in res]
        log("  supply read fell outside the node's recent-state window, retrying")
    raise RuntimeError("could not read token supplies")


def supply_deltas(addrs, a, b):
    """(block, token, net minted) for every mint and burn of the given tokens in blocks a..b."""
    ev = []
    if a > b:
        return ev
    for topics, sign in (([T_TRANSFER, ZERO], 1), ([T_TRANSFER, None, ZERO], -1)):
        for l in get_logs({"address": addrs, "topics": topics}, a, b, SPAN_MANY):
            ev.append((int(l["blockNumber"], 16), l["address"].lower(), sign * int(l["data"], 16)))
    return ev


# ---------------------------------------------------------------- GeckoTerminal
_gt_t = [0.0]
_gt = {"gap": GT_GAP, "deadline": None, "throttled": 0}


def gt_left():
    return float("inf") if _gt["deadline"] is None else _gt["deadline"] - time.time()


def gt(path, tries=6):
    """One GeckoTerminal call. Throttled: slow the pace for the rest of the run, wait, retry. Never past the budget."""
    for k in range(tries):
        if gt_left() <= 0:
            return None
        wait = _gt["gap"] - (time.time() - _gt_t[0])
        if wait > 0:
            time.sleep(wait)
        _gt_t[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(GT_URL + path, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _gt["throttled"] += 1
                _gt["gap"] = min(6.0, _gt["gap"] * 1.25)
                time.sleep(max(0.0, min(15 + 10 * k, gt_left())))
                continue
            if e.code in (400, 404):
                return None
            time.sleep(max(0.0, min(5 + 5 * k, gt_left())))
        except Exception:
            time.sleep(max(0.0, min(5 + 5 * k, gt_left())))
    return None


def gt_tokens(addrs):
    info = {}
    for i in range(0, len(addrs), 30):
        j = gt(f"/networks/{NET}/tokens/multi/{','.join(addrs[i:i + 30])}?include=top_pools")
        for d in (j or {}).get("data", []):
            a = d["attributes"]
            tp = (d.get("relationships", {}).get("top_pools", {}).get("data") or [])
            info[a["address"].lower()] = {
                "price": float(a["price_usd"]) if a.get("price_usd") else None,
                "vol24": float((a.get("volume_usd") or {}).get("h24") or 0),
                "top_pool": tp[0]["id"].split("_", 1)[1] if tp else None}
    return info


def gt_pool_list(path, dex=None):
    j = gt(path)
    if not j:
        return []
    inc = {x["id"]: x["attributes"] for x in j.get("included", [])}
    out = []
    for p in j.get("data", []):
        a, rel = p["attributes"], p["relationships"]
        b, q = rel["base_token"]["data"]["id"], rel["quote_token"]["data"]["id"]
        out.append({"address": a["address"].lower(), "name": a["name"],
                    "dex": dex or ((rel.get("dex") or {}).get("data") or {}).get("id"),
                    "base": b.split("_", 1)[1].lower(), "quote": q.split("_", 1)[1].lower(),
                    "base_sym": inc.get(b, {}).get("symbol"), "quote_sym": inc.get(q, {}).get("symbol"),
                    "vol24": float((a.get("volume_usd") or {}).get("h24") or 0),
                    "reserve": float(a.get("reserve_in_usd") or 0), "created": a.get("pool_created_at")})
    return out


def gt_ohlcv(pool, token=None, limit=100):
    j = gt(f"/networks/{NET}/pools/{pool}/ohlcv/day?aggregate=1&limit={limit}&currency=usd"
           + (f"&token={token}" if token else ""))
    out = {}
    for row in ((j or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list", []):
        out[dt.datetime.fromtimestamp(row[0], dt.timezone.utc).date().isoformat()] = (float(row[4]), float(row[5]))
    return out


# ---------------------------------------------------------------- stages
def stage_base(cutoff):
    """Token list, the cutoff block, day boundaries and every token's supply at the cutoff."""
    latest = int(rpc("eth_blockNumber", []), 16)
    days = [cutoff - dt.timedelta(days=i) for i in range(HISTORY_DAYS - 1, -1, -1)]
    ends = day_end_blocks(days, latest)
    cut = ends[cutoff]
    tokens = load_tokens(cut)
    addrs = [t["address"] for t in tokens]
    L, now = supplies_now(addrs)
    after = {}
    for _, a, v in supply_deltas(addrs, cut + 1, L):
        after[a] = after.get(a, 0) + v
    sup = {a: s - after.get(a, 0) for a, s in zip(addrs, now)}
    save(os.path.join(CACHE, "base.json"), {
        "cutoff": cutoff.isoformat(), "cutoff_block": cut, "read_block": L,
        "days": {d.isoformat(): b for d, b in ends.items()}, "tokens": tokens,
        "supply_cutoff": {a: str(v) for a, v in sup.items()}})
    log(f"base: {len(tokens)} tokens deployed by block {cut:,}, {sum(1 for v in sup.values() if v > 0)} with supply")


def stage_history(cutoff):
    """Each token's supply at the end of every day in the window, rebuilt from mints and burns."""
    base = load(os.path.join(CACHE, "base.json"))
    ends = {dt.date.fromisoformat(d): b for d, b in base["days"].items()}
    path = os.path.join(CACHE, "supply_daily.json")
    cache = load(path, {})
    addrs = [t["address"] for t in base["tokens"]]
    sup = {a: int(v) for a, v in base["supply_cutoff"].items()}
    todo = sorted(d for d in ends if d.isoformat() not in cache)
    if not todo:
        log("supply history up to date")
        return
    ev = sorted(supply_deltas(addrs, ends[todo[0]] + 1, base["cutoff_block"]))
    log(f"history: {len(ev):,} mint and burn events since {todo[0]}")
    acc, j = {}, len(ev)
    for d in sorted(todo, reverse=True):
        while j > 0 and ev[j - 1][0] > ends[d]:
            j -= 1
            _, a, v = ev[j]
            acc[a] = acc.get(a, 0) + v
        row = {}
        for a in addrs:
            s = sup[a] - acc.get(a, 0)
            if s < 0:
                raise SystemExit(f"negative supply for {a} on {d}: events missing")
            if s:
                row[a] = str(s)
        cache[d.isoformat()] = row
    keep = (max(ends) - dt.timedelta(days=100)).isoformat()
    cache = {d: v for d, v in cache.items() if d >= keep}
    save(path, cache)
    log(f"history: {len(todo)} days rebuilt")


def stage_gt(cutoff):
    """Prices, price history, pools, daily pool volume and holder counts from GeckoTerminal.

    Incremental: the cache keeps daily closes and daily pool volume, so a new day fetches only the
    newest candles, today's prices and launchpad pool lists, and holder counts (daily for the largest
    tokens, weekly for the rest). Pool lists for the most-traded tokens refresh weekly.
    """
    base = load(os.path.join(CACHE, "base.json"))
    path = os.path.join(CACHE, "gt.json")
    g = load(path, {})
    if "cutoff" in g and "refreshed_for" not in g:                       # cache written by the first build
        g["refreshed_for"] = g.pop("cutoff")
        g["holders_full_at"] = (g.get("holders_at") or "")[:10]
        g["token_pools_at"] = g["dex_pools_at"] = g["dex_names_at"] = g["refreshed_for"]
    if g.get("refreshed_for") == base["cutoff"] and g.get("holders"):
        save(path, g)
        log("gt: already refreshed for this cutoff")
        return
    today = dt.datetime.now(dt.timezone.utc).date()
    _gt["deadline"] = time.time() + GT_BUDGET
    g["warnings"] = []

    def age(key):
        v = g.get(key)
        return 999 if not v else (today - dt.date.fromisoformat(v[:10])).days

    def need(series, fresh):
        if not series:
            return fresh
        gap = (cutoff - dt.date.fromisoformat(max(series))).days
        return None if gap <= 0 else min(fresh, gap + 2)
    sup = {a: int(v) for a, v in base["supply_cutoff"].items()}
    stock = {t["address"] for t in base["tokens"]}
    live = [t["address"] for t in base["tokens"] if sup.get(t["address"], 0) > 0]
    if age("dex_names_at") >= 7 or not g.get("dex_names"):
        names = {}
        for page in range(1, 5):
            j = gt(f"/networks/{NET}/dexes?page={page}")
            if not j or not j.get("data"):
                break
            for d in j["data"]:
                names[d["id"]] = d["attributes"]["name"].replace(" (Robinhood)", "")
        if names:
            g["dex_names"], g["dex_names_at"] = names, today.isoformat()
    g["info"] = gt_tokens(live)
    if len(g["info"]) < 0.5 * len(live):
        raise SystemExit("GeckoTerminal returned prices for too few stock tokens")
    g["info_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    info = g["info"]
    val = {a: sup[a] / 1e18 * ((info.get(a) or {}).get("price") or 0) for a in live}
    by_val = sorted(live, key=lambda a: -val[a])
    for k in ("px", "price_pool", "vol", "pools", "holders", "token_pools", "dex_pools"):
        g.setdefault(k, {})
    for a in by_val[:PRICE_HISTORY_TOKENS]:
        pool = (info.get(a) or {}).get("top_pool")
        if not pool:
            continue
        if g["price_pool"].get(a) != pool:
            g["px"][a], g["price_pool"][a] = {}, pool
        lim = need(g["px"].get(a), 100)
        if lim:
            o = gt_ohlcv(pool, token=a, limit=lim)
            g["px"].setdefault(a, {}).update({d: c for d, (c, v) in o.items()})
            g["vol"].setdefault(pool, {}).update({d: v for d, (c, v) in o.items()})
    save(path, g)

    def refresh_pool_lists():
        by_vol = sorted(live, key=lambda a: -((info.get(a) or {}).get("vol24") or 0))
        lists = {}
        for a in by_vol[:POOL_LIST_TOKENS]:
            if gt_left() <= 0:
                return
            lists[a] = gt_pool_list(f"/networks/{NET}/tokens/{a}/pools?page=1&sort=h24_volume_usd_desc"
                                    "&include=base_token,quote_token,dex")
        g["token_pools"], g["token_pools_at"] = lists, today.isoformat()
        save(path, g)
    if not g["token_pools"]:
        refresh_pool_lists()
    current = {}
    for lst in g["token_pools"].values():          # each stock token's busiest pools find ~99% of stock-token volume
        for p in lst:
            if p["base"] in stock or p["quote"] in stock:
                current.setdefault(p["address"], p)
    g["pools"].update(current)
    picked = sorted(current.values(), key=lambda p: -p["vol24"])[:VOLUME_POOLS]
    # the pools kept fresh every day; volume totals count only these, so every day in the window counts the same pools
    g["tracked"] = sorted({p["address"] for p in picked}
                          | {g["price_pool"][a] for a in by_val[:PRICE_HISTORY_TOKENS] if a in g["price_pool"]})
    EXACT = 60                                     # the busiest pools get exact daily candles; the rest come in batches
    hold_top, rest = by_val[:HOLDERS_DAILY], by_val[HOLDERS_DAILY:]
    hold_roll = rest[today.toordinal() % 7::7]     # each smaller token at least weekly

    def holders(tokens):
        n_done = 0
        for a in tokens:
            if gt_left() <= 0:
                break
            j = gt(f"/networks/{NET}/tokens/{a}/info")
            try:
                g["holders"][a] = j["data"]["attributes"]["holders"]["count"]
            except Exception:
                pass
            n_done += 1
        save(path, g)
        return n_done
    h_top_done = holders(hold_top)                 # shown on every card, so ahead of the volume long tail
    done = 0
    for n, p in enumerate(picked[:EXACT]):
        if gt_left() <= 0:
            break
        lim = need(g["vol"].get(p["address"]), VOLUME_DAYS + 5)
        if lim:
            o = gt_ohlcv(p["address"], limit=lim)
            if o:
                g["vol"].setdefault(p["address"], {}).update({d: v for d, (c, v) in o.items()})
        done += 1
        if n % 25 == 0:
            save(path, g)
    tail = [p["address"] for p in picked[EXACT:] if cutoff.isoformat() not in g["vol"].get(p["address"], {})]
    tail_done = True
    if today == cutoff + dt.timedelta(days=1):     # 24-hour volume, read the morning after, stands in for the day just closed
        for i in range(0, len(tail), 30):
            if gt_left() <= 0:
                tail_done = False
                break
            j = gt(f"/networks/{NET}/pools/multi/{','.join(tail[i:i + 30])}")
            if j is None:
                tail_done = False
                continue
            for d in j.get("data", []):
                a = d["attributes"]
                g["vol"].setdefault(a["address"].lower(), {})[cutoff.isoformat()] = \
                    float((a.get("volume_usd") or {}).get("h24") or 0)
        save(path, g)
    n_exact = min(EXACT, len(picked))
    log(f"gt: volume for {done} of the {n_exact} busiest pools from daily candles, "
        f"{len(tail)} smaller pools in {(len(tail) + 29) // 30} batch requests")
    h_roll_done = holders(hold_roll)
    g["holders_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    if age("token_pools_at") >= 7:
        refresh_pool_lists()
    left_over = ([f"volume for {n_exact - done} of the busiest pools"] if done < n_exact else []) + \
                (["volume for the smaller pools"] if not tail_done else []) + \
                (["holder counts"] if h_top_done < len(hold_top) or h_roll_done < len(hold_roll) else []) + \
                (["the weekly pool lists"] if age("token_pools_at") >= 7 else [])
    complete = not left_over
    keep_px, keep_vol = (cutoff - dt.timedelta(days=110)).isoformat(), (cutoff - dt.timedelta(days=45)).isoformat()
    g["px"] = {a: {d: v for d, v in s.items() if d >= keep_px} for a, s in g["px"].items()}
    g["vol"] = {p: s2 for p, s2 in ((p, {d: v for d, v in s.items() if d >= keep_vol}) for p, s in g["vol"].items()) if s2}
    g["pools"] = {a: p for a, p in g["pools"].items() if a in g["vol"] or a in current}
    if complete:
        g["refreshed_for"] = base["cutoff"]
    else:
        g["warnings"].append("GeckoTerminal hit its time limit before " + " and ".join(left_over)
                             + "; the next run finishes them. Value on-chain is complete.")
    save(path, g)
    log(f"gt: holders refreshed for the top {HOLDERS_DAILY} tokens and a rolling seventh of the rest; "
        f"{'complete' if complete else 'stopped at the time budget'}"
        + (f"; throttled {_gt['throttled']} times" if _gt["throttled"] else ""))


def _fields(l):
    d = l["data"][2:]
    return list(l["topics"][1:]) + ["0x" + d[i:i + 64] for i in range(0, len(d), 64)]


def stage_launches(cutoff):
    """Launches per day for the last 30 days, and how many paired the new token with a stock token.
    Days already counted are cached in launches_daily.json, so a new day reads one day of events."""
    base = load(os.path.join(CACHE, "base.json"))
    ends = {dt.date.fromisoformat(d): b for d, b in base["days"].items()}
    stock = {t["address"]: t["symbol"] for t in base["tokens"]}
    days = [cutoff - dt.timedelta(days=i) for i in range(VOLUME_DAYS - 1, -1, -1)]
    cpath = os.path.join(CACHE, "launches_daily.json")
    cache = load(cpath, {})
    missing = [d for d in days if d.isoformat() not in cache]
    if missing:
        span = [d for d in days if d >= missing[0]]
        bounds = [ends[d] for d in span]
        fresh = {d.isoformat(): {k: {"all": 0, "stock": 0, "by_stock": {}} for k in LAUNCH_EVENTS} for d in span}
        for key, e in LAUNCH_EVENTS.items():
            ev = get_logs({"address": e["address"], "topics": [e["topic"]]}, ends[span[0] - dt.timedelta(days=1)] + 1,
                          base["cutoff_block"], SPAN_ONE)
            for l in ev:
                q = "0x" + _fields(l)[e["quote"]][-40:]
                i = bisect.bisect_left(bounds, int(l["blockNumber"], 16))
                c = fresh[span[i].isoformat()][key]
                c["all"] += 1
                if q in stock:
                    c["stock"] += 1
                    c["by_stock"][stock[q]] = c["by_stock"].get(stock[q], 0) + 1
        cache.update(fresh)
    keep = (cutoff - dt.timedelta(days=45)).isoformat()
    cache = {d: v for d, v in cache.items() if d >= keep}
    save(cpath, cache)
    out = {}
    for key in LAUNCH_EVENTS:
        rows = [cache[d.isoformat()][key] for d in days]
        per = {}
        for r in rows:
            for k, v in r["by_stock"].items():
                per[k] = per.get(k, 0) + v
        out[key] = {"contract": LAUNCH_EVENTS[key]["address"], "launches": sum(r["all"] for r in rows),
                    "stock_paired": sum(r["stock"] for r in rows), "daily_all": [r["all"] for r in rows],
                    "daily_stock": [r["stock"] for r in rows], "by_stock": sorted(([k, v] for k, v in per.items()), key=lambda kv: -kv[1])}
        log(f"launches: {key}: {out[key]['launches']:,} in 30 days, {out[key]['stock_paired']:,} paired with a stock token")
    save(os.path.join(CACHE, "launches.json"), {"cutoff": base["cutoff"], "days": [d.isoformat() for d in days], "sources": out})


ETF = re.compile(r"\b(ETF|Trust|Fund|iShares|SPDR|Invesco QQQ|Vanguard|ProShares|Direxion|Select Sector)\b", re.I)


def kind_of(name):
    return "ETF" if ETF.search(name) else "Stock"


def stage_assemble(cutoff):
    base = load(os.path.join(CACHE, "base.json"))
    hist = load(os.path.join(CACHE, "supply_daily.json"), {})
    g = load(os.path.join(CACHE, "gt.json"))
    D = [(cutoff - dt.timedelta(days=i)).isoformat() for i in range(HISTORY_DAYS - 1, -1, -1)]
    V = D[-VOLUME_DAYS:]
    toks = {t["address"]: t for t in base["tokens"]}
    stock = set(toks)
    sup = {a: int(v) / 1e18 for a, v in base["supply_cutoff"].items()}      # all stock tokens use 18 decimals
    info = g.get("info", {})
    live = [a for a in toks if sup.get(a, 0) > 0]

    def prices(a):
        cur = (info.get(a) or {}).get("price")
        ser = g.get("px", {}).get(a, {})
        first = next((ser[d] for d in D if d in ser), None)
        out, last = [], None
        for d in D:
            last = ser.get(d, last)
            out.append(last if last is not None else (first if first is not None else cur))
        return out
    P = {a: prices(a) for a in toks}
    rows = []
    for a in live:
        t = toks[a]
        px = P[a][-1]
        rows.append({"address": a, "symbol": t["symbol"], "name": t["name"].split(" \u2022 ")[0].strip(), "kind": kind_of(t["name"]),
                     "supply": sup[a], "price": px, "value": sup[a] * px if px else None,
                     "holders": g.get("holders", {}).get(a), "deployed_block": t["block"]})
    # daily value and live-token count, from the rebuilt supply and each token's daily close
    series = {"dates": D, "value": [], "live": []}
    for i, d in enumerate(D):
        row = hist.get(d)
        if row is None:
            series["value"].append(None); series["live"].append(None)
            continue
        tot = 0.0
        for a, raw in row.items():
            if a in P and P[a][i]:
                tot += int(raw) / 1e18 * P[a][i]
        series["value"].append(round(tot))
        series["live"].append(sum(1 for raw in row.values() if int(raw) > 0))
    # volume: every tracked pool that holds a stock token, counted once
    pools = g.get("pools", {})
    tracked = set(g.get("tracked") or [])
    vol = {p: s for p, s in g.get("vol", {}).items() if not tracked or p in tracked}
    pool_rows, daily = [], [0.0] * len(V)
    by_tok, by_tok7 = {}, {}                   # each token's DEX volume over 30 days, and over the last 7 (the economy read)
    for addr, p in pools.items():
        if addr not in vol:
            continue
        dv = [vol[addr].get(d, 0.0) for d in V]
        v30 = sum(dv)
        daily = [x + y for x, y in zip(daily, dv)]
        sides = [s for s in (p["base"], p["quote"]) if s in stock]
        for s in sides:
            by_tok[s] = by_tok.get(s, 0.0) + v30
            by_tok7[s] = by_tok7.get(s, 0.0) + sum(dv[-7:])
        other = p["quote"] if p["base"] in stock and p["quote"] not in stock else p["base"]
        osym = (p["quote_sym"] if other == p["quote"] else p["base_sym"]) or ""
        if len(sides) == 2:
            kind = "Two stock tokens"
        elif osym.upper() in STABLES:
            kind = "Stablecoin"
        elif osym.upper() in ("WETH", "ETH"):
            kind = "ETH"
        elif any(p["dex"] in dx for dx in LAUNCHPADS.values()):
            kind = "Launchpad token"
        else:
            kind = "Other token"
        pool_rows.append({"address": addr, "name": p["name"], "dex": p["dex"],
                          "dex_name": g.get("dex_names", {}).get(p["dex"], p["dex"]),
                          "stocks": [toks[s]["symbol"] for s in sides], "pair": kind, "vol30": v30,
                          "vol24": p["vol24"], "reserve": p["reserve"], "created": p["created"]})
    for r in rows:
        r["vol30"] = by_tok.get(r["address"], 0.0)
        r["vol7"] = by_tok7.get(r["address"], 0.0)
    pool_rows.sort(key=lambda r: -r["vol30"])
    # launchpads: pools whose other side is a stock token
    lps = {}
    for lp, dexes in LAUNCHPADS.items():
        allp = [p for dx in dexes for p in g.get("dex_pools", {}).get(dx, [])]
        sp = [p for p in allp if p["base"] in stock or p["quote"] in stock]
        cov = [p for p in sp if p["address"] in vol]
        per = {}
        for p in sp:
            s = p["quote"] if p["quote"] in stock else p["base"]
            e = per.setdefault(toks[s]["symbol"], {"pools": 0, "vol30": 0.0})
            e["pools"] += 1
            if p["address"] in vol:
                e["vol30"] += sum(vol[p["address"]].get(d, 0.0) for d in V)
        lps[lp] = {"markets": dexes, "listed_pools": len(allp), "stock_pools": len(sp),
                   "vol24_all": sum(p["vol24"] for p in allp), "vol24_stock": sum(p["vol24"] for p in sp),
                   "vol30_stock": sum(e["vol30"] for e in per.values()),
                   "covered_vol24": sum(p["vol24"] for p in cov),
                   "new_stock_pools_30d": sum(1 for p in sp if p["created"] and p["created"][:10] >= V[0]),
                   "by_stock": sorted(({"symbol": k, **v} for k, v in per.items()), key=lambda e: -e["vol30"])}
    rows.sort(key=lambda r: -(r["value"] or 0))
    out = {"cutoff": base["cutoff"], "cutoff_block": base["cutoff_block"],
           "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
           "factory": FACTORY, "deployed": len(toks), "live": len(live),
           "priced": sum(1 for r in rows if r["value"]), "tokens": rows, "series": series,
           "volume": {"dates": V, "daily": [round(x) for x in daily], "total30": sum(daily),
                      "pools_tracked": len([a for a in pools if a in vol])},
           "pools": pool_rows[:40], "launchpads": lps,
           "prices_at": g.get("info_at"), "holders_at": g.get("holders_at"), "warnings": g.get("warnings") or []}
    out["launches"] = load(os.path.join(CACHE, "launches.json"), {})
    out["holders_full_at"] = g.get("holders_full_at")
    save(os.path.join(DATA, "stocks.json"), out)
    save(os.path.join(CACHE, "daily", f"{base['cutoff']}.json"), out)        # one snapshot per day, kept in the repo
    log(f"assembled stocks.json: {out['live']} live, value ${sum(r['value'] or 0 for r in rows)/1e6:,.1f}M, "
        f"30d volume ${out['volume']['total30']/1e6:,.1f}M across {out['volume']['pools_tracked']} pools")


def resolve_cutoff(s):
    if s:
        return dt.date.fromisoformat(s)
    p = os.path.join(DATA, "rh_series.json")
    if os.path.exists(p):
        v = str(load(p).get("last_full_day") or "")
        for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
            try:
                return dt.datetime.strptime(v[:10] if fmt == "%Y-%m-%d" else v, fmt).date()
            except ValueError:
                pass
    return dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff")
    ap.add_argument("--stage", default="all", choices=["base", "history", "launches", "gt", "assemble", "all"])
    a = ap.parse_args()
    cutoff = resolve_cutoff(a.cutoff)
    log(f"cutoff {cutoff} (end of day UTC)")
    for name, fn in (("base", stage_base), ("history", stage_history), ("launches", stage_launches),
                     ("gt", stage_gt), ("assemble", stage_assemble)):
        if a.stage in (name, "all"):
            fn(cutoff)


if __name__ == "__main__":
    main()
