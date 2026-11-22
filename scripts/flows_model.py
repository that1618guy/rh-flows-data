"""Turn the flows cache into the Flows page model.

Windows end on the latest cached full UTC day: 1D, 7D, and 30D once 30 days are cached. For each token and window,
every wallet that traded is put in a size group by its holding at the window's start or end, whichever is larger,
valued at the price then. End holding = balance at the head snapshot less transfers after the window; start = end
less the wallet's net movement on every day inside the window (all transfers, from the day files' moves).

Net = USD bought less USD sold in the token's main pools. Wallets trading more than 100 times a day, and larger
contracts (arbitrage, market-making, vaults) trading 10+ times a day, are shown apart as bots, never inside a size
group. Smart wallets (EIP-7702 and small wallet proxies) count as wallets.
"""
import datetime as dt
import gzip
import json
import os

GROUPS = [  # id, label, lower bound in USD
    ("whale", "Whales", 50_000),
    ("big", "Big wallets", 5_000),
    ("mid", "Mid wallets", 500),
    ("small", "Small wallets", 0),
]
GROUP_RANGE = {"whale": "$50K+", "big": "$5K to $50K", "mid": "$500 to $5K", "small": "under $500"}
BOT_TRADES_PER_DAY = 100
DUST_USD = 1.0
WALLET_KINDS = {"eoa", "7702", "c62", "c46"}      # EOAs, EIP-7702 wallets, ERC-1967 and EIP-1167 wallet proxies
CATS = [("memes", "Memes"), ("stocks", "Stock tokens"), ("agents", "AI & agents"), ("ecosystem", "Ecosystem")]
SHORT = {"SPCX": "SpaceX", "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ", "MSTR": "Strategy", "GOOGL": "Alphabet",
         "META": "Meta", "CRCL": "Circle", "COIN": "Coinbase", "PLTR": "Palantir", "AMD": "AMD", "TSLA": "Tesla",
         "AAPL": "Apple", "AMZN": "Amazon", "NVDA": "NVIDIA"}


def _group(usd):
    for gid, _, lo in GROUPS:
        if usd >= lo:
            return gid
    return "small"


def _blank():
    return {"net": 0.0, "bought": 0.0, "sold": 0.0, "wallets": 0, "buyers": 0, "sellers": 0, "fresh_n": 0, "fresh_net": 0.0}


def load_token(tdir):
    meta = json.load(open(os.path.join(tdir, "meta.json")))
    with gzip.open(os.path.join(tdir, "head.json.gz"), "rt") as f:
        head = json.load(f)
    days = {}
    for fn in sorted(os.listdir(tdir)):
        if fn[:4].isdigit() and fn.endswith(".json.gz") and fn[:10] <= head["day"]:
            with gzip.open(os.path.join(tdir, fn), "rt") as f:
                days[fn[:10]] = json.load(f)
    return {**meta, "head": head, "days": days}


def token_window(t, wdays):
    """Group totals for one token over the days in wdays (ISO dates, oldest first, ending at the head day)."""
    days = {d: t["days"][d] for d in wdays if d in t["days"]}
    if not days:
        return None
    ds = sorted(days)
    dec = 10 ** t["decimals"]
    p_open = days[ds[0]]["price_open"] or 0
    p_close = days[ds[-1]]["price_close"] or 0
    head = t["head"]; bal = head["bal"]; after = head["after"]; kinds = head["kinds"]
    agg = {gid: _blank() for gid, _, _ in GROUPS}
    agg["bots"] = _blank()
    pt_vol = wallet_vol = 0.0
    traded = {}
    for d in ds:
        for a, row in days[d]["rows"].items():
            b, s, n, nt = row[0], row[1], row[2], row[3]
            r = traded.setdefault(a, [0.0, 0.0, 0, 0])
            r[0] += b; r[1] += s; r[2] += n
            if "moves" not in days[d]:
                r[3] += int(nt)
    for d in ds:                       # every day's movement, including days the wallet didn't trade
        mv = days[d].get("moves")
        if mv:
            for a, r in traded.items():
                if a in mv:
                    r[3] += int(mv[a])
    for a, (b, s, n, moved) in traded.items():
        end = int(bal.get(a, 0)) - int(after.get(a, 0))
        start = end - moved
        start_usd = max(start, 0) / dec * p_open
        end_usd = max(end, 0) / dec * p_close
        per_day = n / len(ds)
        bot = per_day > BOT_TRADES_PER_DAY or (kinds.get(a, "eoa") not in WALLET_KINDS and per_day >= 10)
        g = agg["bots" if bot else _group(max(start_usd, end_usd))]
        net = b - s
        g["net"] += net; g["bought"] += b; g["sold"] += s; g["wallets"] += 1
        if net > DUST_USD:
            g["buyers"] += 1
        elif net < -DUST_USD:
            g["sellers"] += 1
        if not bot:
            wallet_vol += b + s
            if start_usd < DUST_USD and end_usd < DUST_USD:
                pt_vol += b + s          # held none before and none after: passed tokens through
            # fresh: held none at the start, net bought, and still holds at least half of it at the end
            if start_usd < DUST_USD and net > DUST_USD and end_usd >= 0.5 * net:
                g["fresh_n"] += 1; g["fresh_net"] += net
    allw = _blank()
    for gid, _, _ in GROUPS:
        for k in allw:
            allw[k] += agg[gid][k]
    agg["all"] = allw
    sign = 1 if allw["net"] >= 0 else -1
    lead = max((gid for gid, _, _ in GROUPS), key=lambda gid: sign * agg[gid]["net"])
    minted = sum(int(days[d]["mint"]) - int(days[d]["burn"]) for d in ds) / dec * p_close
    chg = (p_close / p_open - 1) * 100 if p_open else None
    out = {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in agg.items()}
    out.update({"lead": lead if abs(agg[lead]["net"]) > 0 else None, "minted": round(minted, 2),
                "swaps": sum(days[d]["swaps"] for d in ds), "days": len(ds),
                "passthrough": round(pt_vol / wallet_vol * 100, 1) if wallet_vol else None,
                "price_chg": round(chg, 2) if chg is not None else None})
    return out


def build(cache_dir):
    toks, stale = [], []
    for sym in sorted(os.listdir(cache_dir)):
        tdir = os.path.join(cache_dir, sym)
        if os.path.isdir(tdir) and os.path.exists(os.path.join(tdir, "head.json.gz")):
            toks.append(load_token(tdir))
    if not toks:
        raise SystemExit("flows: no cached tokens")
    asof = max(t["head"]["day"] for t in toks)
    for t in toks:
        if t["head"]["day"] != asof:
            stale.append(t["symbol"])
    toks = [t for t in toks if t["head"]["day"] == asof]
    unpriced = [t["symbol"] for t in toks if not (t["days"].get(asof) or {}).get("price_close")]
    toks = [t for t in toks if t["symbol"] not in unpriced]
    A = dt.date.fromisoformat(asof)
    span = lambda n: [(A - dt.timedelta(days=i)).isoformat() for i in range(n)][::-1]
    # a window is offered when most tokens have every day of it cached; a token missing any day of an offered
    # window is left out until its history fills in (missing days are retried every run, newest first)
    windows = {"1d": span(1)}
    for wid, n in (("7d", 7), ("30d", 30)):
        full = sum(1 for t in toks if all(d in t["days"] for d in span(n)))
        if full >= 0.8 * len(toks):
            windows[wid] = span(n)
    incomplete = [t["symbol"] for t in toks if not all(d in t["days"] for wd in windows.values() for d in wd)]
    toks = [t for t in toks if t["symbol"] not in incomplete]
    model = {"asof": asof, "windows": {w: {"from": d[0], "to": d[-1]} for w, d in windows.items()}, "stale": stale,
             "unpriced": unpriced, "incomplete": incomplete,
             "groups": [{"id": g, "label": l, "range": GROUP_RANGE[g]} for g, l, _ in GROUPS],
             "cats": [{"id": c, "label": l} for c, l in CATS], "tokens": []}
    for t in toks:
        rec = {"symbol": t["symbol"], "name": SHORT.get(t["symbol"]) or t.get("name") or t["symbol"],
               "category": t["category"], "token": t["token"], "pools": t["main_pools"], "pricing": t["pricing_pool"],
               "price": t["days"][asof]["price_close"] if asof in t["days"] else None, "w": {}}
        for wid, wd in windows.items():
            r = token_window(t, wd)
            if r:
                rec["w"][wid] = r
        if all(w in rec["w"] for w in windows):
            model["tokens"].append(rec)
    return model


def dshort(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def dlong(iso):
    return f"{dshort(iso)}, {iso[:4]}"
