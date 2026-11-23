"""Smart money: the wallets with the best trading results on Robinhood Chain tokens over a selection window, and what
they did in the report window after it. Built from the flows cache; no extra chain reads.

Report window: the latest day, or the latest report_days days. Selection window: every cached day before the report
window (up to 30 days in all). The report window is kept out of the ranking, so "what they bought" is never the
reason they were picked. A longer report window therefore ranks on fewer days; build() returns None when fewer
than min_sel days are left to rank on.

Trading result for a wallet in one token over the selection window, counting only tokens it bought in the window:
    realized = min(tokens bought, tokens sold) x (average sell price - average buy price)
    open     = (tokens bought - tokens sold, if positive) x (close at the end of the window - average buy price)
Selling more than it bought in the window (old holdings, or tokens received by transfer) has no known cost, so it
isn't counted: selling an airdrop or a team allocation is never "profit". Token amounts per side are exact for days
the engine stored them (bought_raw, sold_raw); older days use the day's net movement when it matches the trades,
or the trades' USD at the day's average price.

Smart money: up to 100 wallets with the best total result (at least $500) that traded $5K+ with 3+ trades on 2+
days, excluding bots (more than
100 trades a day in a token) and contracts other than smart wallets.

In the report window, bots are left out by the Flows page's own rule for those days, so what the smart wallets did
plus what everyone else did adds up to the Flows page's all-wallets net.

The book: what those wallets still hold of what they bought since the window opened, the latest day included,
valued at the latest close. Entry is the average price they paid for it, so "up 12% on entry" is their open result.
"""
import collections, json, os
import flows_model as FM

N_SMART = 100
MIN_VOL = 5_000
MIN_TRADES = 3
MIN_DAYS = 2
MIN_RESULT = 500
MATCH = 0.25


def _result(x, P):
    b, s, qb, qs = x
    if qb <= 0 and qs <= 0:
        return 0.0
    pb = b / qb if qb > 0 else 0
    ps = s / qs if qs > 0 else 0
    return min(qb, qs) * (ps - pb) + max(qb - qs, 0) * (P - pb)


def _avg(a, b):
    xs = [x for x in (a, b) if x]
    return sum(xs) / len(xs) if xs else None


BOOK_MIN_USD = 50                # a position smaller than this isn't counted as holding the token


def _qty(r, dec, pbar):
    """Tokens bought and sold in one day row: exact where the engine stored them, estimated otherwise."""
    b, s, nt = r[0], r[1], int(r[3])
    if len(r) >= 6:
        return int(r[4]) / dec, int(r[5]) / dec
    q_net = nt / dec
    clean = (b + s) > 0 and abs(q_net * pbar - (b - s)) <= MATCH * (b + s)
    return (q_net if clean and s == 0 and q_net > 0 else b / pbar), (-q_net if clean and b == 0 and q_net < 0 else s / pbar)


MIN_SEL = 7                      # days of ranking a report window longer than a day needs before it is offered


def build(cache_dir, n_smart=N_SMART, max_days=30, symbols=None, report_days=1, min_sel=None):
    toks, codes = [], {}
    cp = os.path.join(cache_dir, "codes.json")
    if os.path.exists(cp):
        codes = json.load(open(cp))
    for sym in sorted(os.listdir(cache_dir)):
        tdir = os.path.join(cache_dir, sym)
        if os.path.isdir(tdir) and os.path.exists(os.path.join(tdir, "head.json.gz")):
            toks.append(FM.load_token(tdir))
    if symbols is not None:
        toks = [t for t in toks if t["symbol"] in symbols]
    asof = max(t["head"]["day"] for t in toks)
    toks = [t for t in toks if t["head"]["day"] == asof and (t["days"].get(asof) or {}).get("price_close")]
    # the window: latest consecutive days that most tokens have
    import datetime as dt
    A = dt.date.fromisoformat(asof)
    W = []
    for i in range(max_days):
        d = (A - dt.timedelta(days=i)).isoformat()
        if sum(1 for t in toks if d in t["days"]) < 0.8 * len(toks):
            break
        W.append(d)
    W = W[::-1]
    if len(W) < 3:
        raise SystemExit("smart money: need at least 3 cached days")
    if report_days > 1 and len(W) - report_days < (MIN_SEL if min_sel is None else min_sel):
        return None                                     # not enough days left to rank the wallets on
    sel, rep, last = W[:-report_days], W[-report_days:], W[-1]
    toks = [t for t in toks if all(d in t["days"] for d in W)]
    W_KINDS = FM.WALLET_KINDS
    wal = collections.defaultdict(lambda: {"vol": 0.0, "n": 0, "pos": collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]),
                                           "bot": False, "days": set()})
    closes = {}
    for t in toks:
        sym, dec = t["symbol"], 10 ** t["decimals"]
        kinds = t["head"]["kinds"]
        closes[sym] = t["days"][sel[-1]]["price_close"] or 0
        for d in sel:
            day = t["days"][d]
            pbar = _avg(day["price_open"], day["price_close"])
            if not pbar:
                continue
            for a, r in day["rows"].items():
                b, s, n, nt = r[0], r[1], r[2], int(r[3])
                w = wal[a]
                kind = kinds.get(a) or codes.get(a, "eoa")
                if n > FM.BOT_TRADES_PER_DAY or kind not in W_KINDS:
                    w["bot"] = True
                qb, qs = _qty(r, dec, pbar)
                x = w["pos"][sym]
                x[0] += b; x[1] += s; x[2] += qb; x[3] += qs
                w["vol"] += b + s; w["n"] += n; w["days"].add(d)
    for a, w in wal.items():
        w["tok"] = collections.Counter({sym: _result(x, closes[sym]) for sym, x in w["pos"].items()})
        w["res"] = sum(w["tok"].values())
    ok = [(a, w) for a, w in wal.items() if not w["bot"] and w["vol"] >= MIN_VOL and w["n"] >= MIN_TRADES and len(w["days"]) >= MIN_DAYS]
    ok.sort(key=lambda x: -x[1]["res"])
    smart = [(a, w) for a, w in ok[:n_smart] if w["res"] >= MIN_RESULT]
    S = {a for a, _ in smart}
    bots = {a for a, w in wal.items() if w["bot"]}
    # what they did in the report window
    tokens, day_by_wallet = [], collections.defaultdict(collections.Counter)
    for t in toks:
        sm = {"net": 0.0, "bought": 0.0, "sold": 0.0, "buyers": 0, "sellers": 0}
        cr = {"net": 0.0, "bought": 0.0, "sold": 0.0, "buyers": 0, "sellers": 0}
        did, kinds = {}, t["head"]["kinds"]
        for d in rep:
            for a, r in t["days"][d]["rows"].items():
                x = did.setdefault(a, [0.0, 0.0, 0])
                x[0] += r[0]; x[1] += r[1]; x[2] += r[2]
        for a, (b, s, n) in did.items():
            # bots are left out by the Flows page's rule for this window, so "they" plus "everyone else" is exactly
            # the Flows page's all-wallets net for the same token and days
            per_day = n / len(rep)
            if per_day > FM.BOT_TRADES_PER_DAY or (kinds.get(a, "eoa") not in W_KINDS and per_day >= 10):
                continue
            g = sm if a in S else cr
            g["net"] += b - s; g["bought"] += b; g["sold"] += s
            if b - s > 1:
                g["buyers"] += 1
            elif b - s < -1:
                g["sellers"] += 1
            if a in S:
                day_by_wallet[a][t["symbol"]] += b - s
        po, pc = t["days"][rep[0]]["price_open"], t["days"][last]["price_close"]
        tokens.append({"symbol": t["symbol"], "name": FM.SHORT.get(t["symbol"]) or t.get("name") or t["symbol"],
                       "category": t["category"], "token": t["token"],
                       "smart": {k: round(v, 2) for k, v in sm.items()}, "crowd": {k: round(v, 2) for k, v in cr.items()},
                       "chg": round((pc / po - 1) * 100, 2) if po and pc else None})
    # the book: what they still hold of what they bought since the window opened, the report window included
    crowd_net = {x["symbol"]: x["crowd"]["net"] for x in tokens}
    for x in tokens:
        t = next(t for t in toks if t["symbol"] == x["symbol"])
        sym, dec = t["symbol"], 10 ** t["decimals"]
        P = t["days"][last]["price_close"] or 0
        held = []
        for a, w in smart:
            b, _s, qb, qs = w["pos"].get(sym, (0.0, 0.0, 0.0, 0.0))
            for d in rep:
                day = t["days"][d]
                pbar = _avg(day["price_open"], day["price_close"])
                r = day["rows"].get(a)
                if r and pbar:
                    q1, q2 = _qty(r, dec, pbar)
                    b += r[0]; qb += q1; qs += q2
            open_q = qb - qs
            if open_q > 0 and qb > 0 and open_q * P >= BOOK_MIN_USD:
                held.append((a, open_q * P, b / qb, open_q))
        usd = sum(h[1] for h in held)
        q = sum(h[3] for h in held)
        entry = sum(h[3] * h[2] for h in held) / q if q else None
        held.sort(key=lambda h: -h[1])
        x["book"] = {"wallets": len(held), "usd": round(usd, 2), "entry": entry, "price": P,
                     "pl": round((P / entry - 1) * 100, 1) if entry else None,
                     "top": [[a, round(u, 2), round((P / c - 1) * 100, 1) if c else None] for a, u, c, _ in held[:5]]}
    board = []
    for a, w in smart[:25]:
        best = w["tok"].most_common(1)[0] if w["tok"] else (None, 0)
        dmov = day_by_wallet.get(a, collections.Counter())
        top = max(dmov.items(), key=lambda kv: abs(kv[1])) if dmov else (None, 0)
        board.append({"a": a, "res": round(w["res"], 2), "vol": round(w["vol"], 2), "n": w["n"], "days": len(w["days"]),
                      "ntok": sum(1 for v in w["tok"].values() if v != 0), "wins": sum(1 for v in w["tok"].values() if v > 0),
                      "best": best[0], "best_res": round(best[1], 2),
                      "day_net": round(sum(dmov.values()), 2), "day_top": top[0], "day_top_net": round(top[1], 2)})
    active = sum(1 for a in S if a in day_by_wallet)
    return {"asof": asof, "sel": {"from": sel[0], "to": sel[-1]}, "day": last, "n_smart": len(S), "active": active,
            "rep": {"from": rep[0], "to": last, "days": len(rep)},
            "eligible": len(ok), "criteria": {"min_vol": MIN_VOL, "min_trades": MIN_TRADES, "min_days": MIN_DAYS},
            "total_res": round(sum(w["res"] for _, w in smart), 2), "total_vol": round(sum(w["vol"] for _, w in smart), 2),
            "cats": [{"id": c, "label": l} for c, l in FM.CATS], "tokens": tokens, "board": board,
            "cut": {"res": round(smart[-1][1]["res"], 2) if smart else None, "positive": sum(1 for _, w in ok if w["res"] > 0)}}
