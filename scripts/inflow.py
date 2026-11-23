"""Inflow score: is money coming into Robinhood Chain or leaving, on one fixed 0-100 scale. 50 is balanced.

Three parts, each on its own fixed scale, so a score means the same thing every day:
  capital      (40%)  7-day net flow of stablecoins and ETH onto the chain, as a share of the capital already there.
                      -3% scores 0, flat scores 50, +3% scores 100.
  buying       (35%)  of what wallets traded in RH tokens over 7 days, the share that was buying.
                      45% scores 0, 50% scores 50, 55% scores 100. Bots are left out.
  new wallets  (25%)  wallets that took a first position ($10+) in a tracked token this week, against the weekly
                      average of the four weeks before. -50% scores 0, level scores 50, +50% scores 100.

A part without data is left out and the others are reweighted.
"""
import datetime as dt, gzip, json, os

WEIGHTS = {"capital": 0.40, "buying": 0.35, "wallets": 0.25}
LABELS = {"capital": "Capital onto the chain", "buying": "Buying of RH tokens", "wallets": "New wallets"}
SCALES = {"capital": (-3.0, 3.0), "buying": (45.0, 55.0), "wallets": (-50.0, 50.0)}
MIN_LIQ = 50_000                 # tokens with less pool liquidity than this are left out of every token view
MIN_FIRST_USD = 10.0
WALLET_KINDS = {"eoa", "7702", "c62", "c46"}
IN, OUT = 60, 40


def lin(x, lo, hi):
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _prev(d, n=1):
    return (dt.date.fromisoformat(d) - dt.timedelta(days=n)).isoformat()


def capital_part(rows, i=None):
    """rows: capital.py rows. i: index of the window's last day (default the latest)."""
    i = len(rows) - 1 if i is None else i
    if i < 7:
        return None
    w = rows[i - 6:i + 1]
    last = rows[i]
    stock = last["stables_stock"] + (last["eth_units"] or 0) * (last["eth_px"] or 0)
    net = sum(r["net"] for r in w)
    if stock <= 0:
        return None
    pct = net / stock * 100
    return {"score": lin(pct, *SCALES["capital"]), "value": pct, "net": net, "stock": stock,
            "stables": sum(r["stables"] for r in w), "eth": sum(r["eth"] or 0 for r in w)}


def buying_part(tokens, wid="7d"):
    """tokens: flows model tokens (already filtered to the ones shown)."""
    b = s = 0.0
    for t in tokens:
        w = t["w"].get(wid)
        if w:
            b += w["all"]["bought"]; s += w["all"]["sold"]
    if b + s <= 0:
        return None
    share = b / (b + s) * 100
    return {"score": lin(share, *SCALES["buying"]), "value": share, "net": b - s, "bought": b, "sold": s}


def new_wallets(states_dir, symbols, asof, days=35):
    """{day: wallets whose first position in any of these tokens was that day and worth $10+}, last `days` days."""
    first = {}
    for sym in symbols:
        p = os.path.join(states_dir, f"{sym}.state.json.gz")
        if not os.path.exists(p):
            continue
        with gzip.open(p, "rt") as f:
            st = json.load(f)
        if st.get("asof") != asof or not st.get("rebuildable", True):
            continue
        dec = 10 ** st["decimals"]; closes = st["closes"]; kinds = st.get("kinds", {})
        pools = set(st.get("pools") or [])
        for a, x in st["addrs"].items():
            fi = x[1]
            if not fi or a in pools or kinds.get(a, "eoa") not in WALLET_KINDS:
                continue
            got = x[4].get(fi)
            px = closes.get(fi)
            if got is None or not px or int(got) / dec * px < MIN_FIRST_USD:
                continue
            if a not in first or fi < first[a]:
                first[a] = fi
    cut = _prev(asof, days - 1)
    out = {_prev(asof, k): 0 for k in range(days)}
    for a, d in first.items():
        if cut <= d <= asof:
            out[d] += 1
    return dict(sorted(out.items()))


def wallets_part(series):
    ds = sorted(series)
    if len(ds) < 35:
        return None
    week = sum(series[d] for d in ds[-7:])
    base = sum(series[d] for d in ds[-35:-7]) / 4
    if base <= 0:
        return None
    chg = (week / base - 1) * 100
    return {"score": lin(chg, *SCALES["wallets"]), "value": chg, "week": week, "base": base}


def combine(parts):
    have = {k: p for k, p in parts.items() if p}
    if not have:
        return None
    wsum = sum(WEIGHTS[k] for k in have)
    return sum(p["score"] * WEIGHTS[k] for k, p in have.items()) / wsum


def state(score):
    return "Inflow" if score >= IN else "Outflow" if score <= OUT else "Balanced"


def capital_history(rows, n=120):
    """The capital part's score for each of the last n days: the one part with a long history."""
    out = []
    for i in range(max(7, len(rows) - n), len(rows)):
        p = capital_part(rows, i)
        if p:
            out.append([rows[i]["d"], round(p["score"], 1), round(p["net"])])
    return out
