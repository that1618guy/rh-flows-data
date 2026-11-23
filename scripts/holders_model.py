"""Holder strength: a 0-100 read of how a token's holders behave, from its holder state (holders_state.py).

Holders are wallets (EOAs, EIP-7702 wallets and small wallet proxies) holding the token; pools, the Uniswap v4 pool
manager, other contracts, the token itself and burn addresses are left out. Balances at the end of past days are
rebuilt from the state's balance less each later day's net movement.

Four parts, each 0-100, on fixed scales so a score means the same thing for every token and every day:
  Conviction (35%)   share of holder supply held 30+ days by wallets that moved none out in those 30 days.
                     0% -> 0, 80%+ -> 100
  Top holders (25%)  how the 50 largest holders of a week ago changed their combined balance since.
                     -10% or worse -> 0, flat -> 50, +10% or better -> 100
  Breadth (20%)      holders worth $10+ now vs a week ago (at today's price), averaged with how many of the wallets
                     that became holders 8-14 days ago still hold. -10% -> 0, flat -> 50, +10% -> 100; 0% kept -> 0, 80%+ kept -> 100
  In profit (20%)    share of holder supply whose last top-up was at or below today's price. 0% -> 0, 100% -> 100
The same parts are worked out as of a week earlier, so each token's read says what moved.

Where holders stand (facts, not part of the score): each holder's entry is the price on the day of their last
top-up. `stand` splits holder supply into down 50%+, down less than 50%, up less than 2x, and up 2x or more against
that entry; `avg_pl` is today's price against the supply-weighted average entry.
"""
import datetime as dt, gzip, json

WALLET_KINDS = {"eoa", "7702", "c62", "c46"}
BURN = {"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"}
WEIGHTS = {"conviction": 0.35, "top": 0.25, "breadth": 0.20, "profit": 0.20}
LABELS = {"conviction": "Conviction", "top": "Top holders", "breadth": "Breadth", "profit": "In profit"}


def lin(x, lo, hi):
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def shift(d, n):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=n)).isoformat()


def load(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def balances_at(st, d):
    """Balance of every address at the end of UTC day d (d no later than the state's day)."""
    out = {}
    for a, (bal, _fi, _li, _lo, daily) in st["addrs"].items():
        b = bal - sum(v for k, v in daily.items() if k > d)
        if b > 0:
            out[a] = b
    return out


def price_on(closes, d):
    if d in closes:
        return closes[d]
    prev = [k for k in closes if k <= d]
    return closes[max(prev)] if prev else (closes[min(closes)] if closes else None)


def parts(st, d, price):
    dec = 10 ** st["decimals"]
    kinds, pools = st["kinds"], st["pools_set"]
    holder = lambda a: a not in BURN and a != st["token"] and a not in pools and kinds.get(a, "eoa") in WALLET_KINDS
    now = {a: b for a, b in balances_at(st, d).items() if holder(a)}
    supply = sum(now.values()) or 1
    d30 = shift(d, -30)
    then = balances_at(st, d30)
    conv = 0
    for a, b in now.items():
        b30 = then.get(a, 0)
        if b30 <= 0:
            continue
        if any(d30 < k <= d and v < 0 for k, v in st["addrs"][a][4].items()):
            continue
        conv += min(b, b30)
    conviction = conv / supply * 100
    week = {a: b for a, b in balances_at(st, shift(d, -7)).items() if holder(a)}
    top = sorted(week.items(), key=lambda kv: -kv[1])[:50]
    t0 = sum(b for _, b in top) or 1
    top_chg = (sum(now.get(a, 0) for a, _ in top) / t0 - 1) * 100
    ten = 10 / price * dec if price else float("inf")
    h_now = sum(1 for b in now.values() if b >= ten)
    h_week = sum(1 for b in week.values() if b >= ten) or 1
    growth = (h_now / h_week - 1) * 100
    b14 = {a for a, b in balances_at(st, shift(d, -14)).items() if b >= ten}
    b8 = {a for a, b in balances_at(st, shift(d, -8)).items() if b >= ten and holder(a)}
    newbies = b8 - b14
    kept = sum(1 for a in newbies if now.get(a, 0) >= ten) / len(newbies) * 100 if newbies else None
    prof = 0
    stand, known, cost = [0, 0, 0, 0], 0, 0.0
    for a, b in now.items():
        _bal, fi, li, _lo, daily = st["addrs"][a]
        tops = [k for k, v in daily.items() if v > 0 and k <= d]
        when = max(tops) if tops else (li if li and li <= d else fi)
        p = price_on(st["closes"], when) if when else None
        if p and p <= price:
            prof += b
        if p and price:
            r = price / p
            stand[0 if r < 0.5 else 1 if r < 1 else 2 if r < 2 else 3] += b
            known += b; cost += b * p
    in_profit = prof / supply * 100
    avg_pl = (price / (cost / known) - 1) * 100 if known and cost else None
    top10 = sum(sorted(now.values(), reverse=True)[:10]) / supply * 100
    sc = {"conviction": lin(conviction, 0, 80), "top": lin(top_chg, -10, 10),
          "breadth": (lin(growth, -10, 10) + (lin(kept, 0, 80) if kept is not None else 50)) / 2,
          "profit": lin(in_profit, 0, 100)}
    return {"score": round(sum(sc[k] * w for k, w in WEIGHTS.items()), 1), "parts": {k: round(v, 1) for k, v in sc.items()},
            "facts": {"conviction": round(conviction, 1), "top_chg": round(top_chg, 1), "holders": h_now,
                      "holders_chg": round(growth, 1), "kept": round(kept, 1) if kept is not None else None,
                      "new": len(newbies), "in_profit": round(in_profit, 1), "top10": round(top10, 1),
                      "stand": [round(x / known * 100, 1) for x in stand] if known else None,
                      "avg_pl": round(avg_pl, 1) if avg_pl is not None else None}}


def priced(st):
    """Enough daily closes to value holders: 30+ days of them, the latest within 3 days of the state's day."""
    ks = [k for k in st["closes"] if k <= st["asof"]]
    return len(ks) >= 30 and max(ks) >= shift(st["asof"], -3)


def token(st):
    st["pools_set"] = set(st["pools"])
    d = st["asof"]
    if not priced(st):
        return None
    p_now, p_wk = price_on(st["closes"], d), price_on(st["closes"], shift(d, -7))
    if not p_now:
        return None
    return {"symbol": st["symbol"], "token": st["token"], "category": st["category"], "price": p_now,
            "price_chg_7d": round((p_now / p_wk - 1) * 100, 1) if p_wk else None, "asof": d,
            "now": parts(st, d, p_now), "week_ago": parts(st, shift(d, -7), p_wk), "transfers": st.get("transfers")}


def what_moved(t):
    """One sentence on the part that moved the score most this week."""
    a, b = t["now"], t["week_ago"]
    dd = {k: (a["parts"][k] - b["parts"][k]) * WEIGHTS[k] for k in WEIGHTS}
    total = a["score"] - b["score"]
    same = [x for x in dd if dd[x] * total > 0]          # explain the move with a part that pushed the same way
    k = max(same or dd, key=lambda x: abs(dd[x]))
    f, g = a["facts"], b["facts"]
    if abs(dd[k]) < 1:
        return "Little changed this week."
    if k == "conviction":
        return (f"Conviction {'rose' if dd[k] > 0 else 'fell'}: {f['conviction']:.0f}% of holder supply has sat untouched "
                f"for 30+ days, from {g['conviction']:.0f}% a week ago.")
    if k == "top":
        x, y = f["top_chg"], g["top_chg"]
        if x < -0.05 and y < -0.05:
            return f"The 50 largest holders cut {abs(x):.1f}% this week, {'less' if x > y else 'more'} than the {abs(y):.1f}% the week before."
        if x >= 0.05 and y >= 0.05:
            return f"The 50 largest holders added {x:.1f}% this week, {'more' if x > y else 'less'} than the {y:.1f}% the week before."
        if abs(y) < 0.05:
            return f"The 50 largest holders {'added' if x >= 0 else 'cut'} {abs(x):.1f}% this week, after a flat week before."
        if abs(x) < 0.05:
            return f"The 50 largest holders held flat this week, after {'adding' if y >= 0 else 'cutting'} {abs(y):.1f}% the week before."
        return f"The 50 largest holders {'added' if x >= 0 else 'cut'} {abs(x):.1f}% this week, after {'adding' if y >= 0 else 'cutting'} {abs(y):.1f}% the week before."
    if k == "breadth":
        s = f"Holders worth $10+ {'grew' if f['holders_chg'] >= 0 else 'shrank'} {abs(f['holders_chg']):.1f}% to {f['holders']:,}"
        return s + (f", and {f['kept']:.0f}% of last week's new holders are still in." if f["kept"] is not None else ".")
    return f"{f['in_profit']:.0f}% of holder supply is in profit, {'up' if f['in_profit'] >= g['in_profit'] else 'down'} from {g['in_profit']:.0f}% a week ago."


def why(t):
    """A short clause on the part that pushed this week's move: 'as its 50 largest holders cut 11.5%'."""
    a, b = t["now"], t["week_ago"]
    dd = {k: (a["parts"][k] - b["parts"][k]) * WEIGHTS[k] for k in WEIGHTS}
    total = a["score"] - b["score"]
    k = max([x for x in dd if dd[x] * total > 0] or dd, key=lambda x: abs(dd[x]))
    f, g = a["facts"], b["facts"]
    if k == "conviction":
        return f"as untouched supply {'rose' if f['conviction'] >= g['conviction'] else 'fell'} to {f['conviction']:.0f}%"
    if k == "top":
        return f"as its 50 largest holders {'added' if f['top_chg'] >= 0 else 'cut'} {abs(f['top_chg']):.1f}%"
    if k == "breadth":
        return f"as holders worth $10+ {'grew' if f['holders_chg'] >= 0 else 'shrank'} {abs(f['holders_chg']):.1f}%"
    return f"as supply in profit {'rose' if f['in_profit'] >= g['in_profit'] else 'fell'} to {f['in_profit']:.0f}%"
