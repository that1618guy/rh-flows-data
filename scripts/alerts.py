"""Positioning alerts: the days a token's larger wallets changed what they were doing.

Large wallets hold $5K or more of the token: the Flows page's whales ($50K+) and big wallets ($5K to $50K). For each
token and each cached day their net (USD bought less USD sold in the token's main pools) is rebuilt with every
wallet sized by what it held that day and bots left out. A day raises at most one alert per token:

  turn   a big day against their net over the 5 days before, which was big too
  run    3 or more days running on the same side that add up to a big amount; raised on days 3, 5, 7, 10 and 14
         of a run, and on any day of a run that is big itself
  size   a big day

Big means max($20K, 0.5% of the token's market cap that day). Every alert carries its figures and a plain sentence;
post_alerts.py sends each new day's alerts to Discord.

Usage, to look at what it finds: alerts.py <cache_dir>
"""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows_model as FM, inflow as IN, token_meta as TM
from site_shell import fm

LARGE = ("whale", "big")         # holding $5K+ of the token
MIN_USD = 20_000                 # a big day is at least this ...
MIN_CAP = 0.5                    # ... and at least this % of the token's market cap
PRIOR = 5                        # days before, for a turn
PRIOR_MIN = 3
RUN_MIN = 3                      # days running
RUN_MARKS = (3, 5, 7, 10, 14)    # a quiet run is raised on these days only, so it doesn't repeat every day
QUIET = 1_000                    # a day under this breaks a run
CROWD = 0.25                     # smaller wallets took this share of the other side, and at least CROWD_USD
CROWD_USD = 5_000
KEEP = 14                        # days of alerts worked out each run


def day_views(t):
    """{day: flows_model.token_window for that one day}, with every wallet's holding rebuilt as of that day's end
    (the head balance less everything it moved afterwards), so wallets are sized by what they held then."""
    after, out = dict(t["head"]["after"]), {}
    for d in sorted(t["days"], reverse=True):
        out[d] = FM.token_window({**t, "head": {**t["head"], "after": after}}, [d])
        after = dict(after)
        mv = t["days"][d].get("moves")
        if mv:
            for a, v in mv.items():
                after[a] = int(after.get(a, 0)) + int(v)
        else:                                            # an older day file: only traders' net movement is known
            for a, row in t["days"][d]["rows"].items():
                after[a] = int(after.get(a, 0)) + int(row[3])
    return out


def series(t, size):
    """One token's days, oldest first: what its large wallets and its smaller ones did, and its market cap then."""
    views = day_views(t)
    last = max(t["days"]); p_now = t["days"][last]["price_close"] or 0
    out = []
    for d in sorted(views):
        w = views[d]
        if not w:
            continue
        big = {k: sum(w[g][k] for g in LARGE) for k in ("net", "buyers", "sellers", "fresh_n")}
        p = t["days"][d]["price_close"] or p_now
        out.append({"d": d, "net": big["net"], "buyers": big["buyers"], "sellers": big["sellers"], "fresh": big["fresh_n"],
                    "whale": w["whale"]["net"], "big": w["big"]["net"], "rest": w["mid"]["net"] + w["small"]["net"],
                    "cap": size * p / p_now if p_now and size else size, "chg": w["price_chg"]})
    return out


def _share(x):
    return "under 0.01%" if x < 0.01 else f"{x:.2f}%" if x < 1 else f"{x:.1f}%"


def _next(d):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()


def detect(sym, ser):
    """Alerts for one token, oldest first."""
    out = []
    for i, x in enumerate(ser):
        n = x["net"]
        if not x["cap"] or abs(n) < QUIET:
            continue
        need = max(MIN_USD, MIN_CAP / 100 * x["cap"])
        big = abs(n) >= need
        sgn = 1 if n > 0 else -1
        k, cum, j = 0, 0.0, i                              # the run that ends today, over consecutive days only
        while j >= 0 and ser[j]["net"] * sgn >= QUIET and (j == i or _next(ser[j]["d"]) == ser[j + 1]["d"]):
            cum += ser[j]["net"]; k += 1; j -= 1
        prior = [p for p in ser[max(0, i - PRIOR):i]]
        gapless = all(_next(a["d"]) == b["d"] for a, b in zip(prior, prior[1:] + [x]))
        psum = sum(p["net"] for p in prior)
        turn = big and gapless and len(prior) >= PRIOR_MIN and psum * n < 0 and abs(psum) >= need
        run = k >= RUN_MIN and abs(cum) >= need and (big or k in RUN_MARKS)
        kind = "turn" if turn else "run" if run else "size" if big else None
        if not kind:
            continue
        buy = n > 0
        usd = cum if kind == "run" else n
        share = abs(usd) / x["cap"] * 100
        did, undid = ("bought", "sold") if buy else ("sold", "bought")
        if kind == "turn":
            tag = "Turned buyers" if buy else "Turned sellers"
            text = (f"Large wallets turned {'buyers' if buy else 'sellers'} of {sym}: {fm(abs(n))} {did}, "
                    f"after {fm(abs(psum))} {undid} over the {len(prior)} days before.")
        elif kind == "run":
            tag = f"{'Buying' if buy else 'Selling'}, {k} days running"
            text = f"Large wallets have {did} {sym} {k} days running: {fm(abs(cum))}, {_share(share)} of its market cap."
        else:
            tag = "Big buying day" if buy else "Big selling day"
            text = f"Large wallets {did} {fm(abs(n))} of {sym}, {_share(share)} of its market cap."
        rest = x["rest"]
        if rest * n < 0 and abs(rest) >= max(CROWD_USD, CROWD * abs(n)):
            more = f"Smaller wallets {undid} {fm(abs(rest))} the other way."
        elif x["buyers"] + x["sellers"] >= 3:
            more = f"Of the large wallets that traded, {x['buyers']} bought and {x['sellers']} sold."
        else:
            more = ""
        out.append({"d": x["d"], "s": sym, "kind": kind, "side": "buy" if buy else "sell", "usd": round(usd),
                    "day": round(n), "share": round(share, 3), "run": k if kind == "run" else None, "tag": tag,
                    "prior": round(psum) if kind == "turn" else None, "prior_days": len(prior) if kind == "turn" else None,
                    "text": text, "more": more, "whale": round(x["whale"]), "big": round(x["big"]),
                    "rest": round(rest), "buyers": x["buyers"], "sellers": x["sellers"], "fresh": x["fresh"],
                    "chg": x["chg"], "cap": round(x["cap"])})
    return out


def build(cache_dir, flows=None, meta=None):
    """{"asof", "from", "alerts": newest day first and largest for its size first, "series": {symbol: [[day, net]]}}
    for the tokens every token view shows."""
    flows = flows or FM.build(cache_dir)
    meta = meta or json.load(open(os.path.join(cache_dir, "tokens_meta.json")))
    left = IN.passed_through(flows)
    shown = {t["symbol"] for t in flows["tokens"] if t["category"] != "stocks" and t["symbol"] not in left
             and (meta.get(t["symbol"]) or {}).get("liq", 0) >= IN.MIN_LIQ}
    asof = flows["asof"]
    first = (dt.date.fromisoformat(asof) - dt.timedelta(days=KEEP - 1)).isoformat()
    alerts, ser_out, days = [], {}, set()
    for name in sorted(os.listdir(cache_dir)):
        tdir = os.path.join(cache_dir, name)
        if not (os.path.isdir(tdir) and os.path.exists(os.path.join(tdir, "head.json.gz"))):
            continue
        t = FM.load_token(tdir)
        if t["symbol"] not in shown:
            continue
        ser = series(t, TM.size(meta[t["symbol"]]) or 0)
        days.update(x["d"] for x in ser)
        ser_out[t["symbol"]] = [[x["d"], round(x["net"])] for x in ser if x["d"] >= first]
        alerts += [a for a in detect(t["symbol"], ser) if a["d"] >= first]
    alerts.sort(key=lambda a: (a["d"], a["share"]), reverse=True)
    return {"asof": asof, "from": max(first, min(days)) if days else asof, "alerts": alerts, "series": ser_out,
            "tokens": sorted(shown)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    m = build(sys.argv[1])
    print(f"alerts: {len(m['alerts'])} from {m['from']} to {m['asof']}, across {len(m['tokens'])} tokens")
    for a in m["alerts"]:
        print(f"  {a['d']} {a['s']:9s} {a['kind']:4s} {a['side']:4s} {a['usd']:>+10,d} {a['share']:5.2f}%  {a['text']} {a['more']}")
