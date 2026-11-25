"""Smart Filter: the tokens worth a look today, and why.

Every other token view answers "what did this group do". This one turns it around: for each token and each day it
asks four separate groups what they did, sets that against price, and keeps the token only when the picture adds up
to something.

The four groups (all already tracked; no new chain reads):
  large wallets   wallets holding $5K+ of the token: their net buying in the token's main pools (the Flows page)
  smart money     the wallets with the best trading results before that day (the Smart money page)
  top holders     the 50 largest holders of a week earlier: how their combined balance changed (the Holders page)
  new wallets     wallets that held none, bought, and kept it (the Flows page)

A token that passes one of the filters below is flagged. Each token gets one line a day at most, the same story
isn't repeated on following days, and at most MAX_DAY lines are kept per day. On most days few tokens are
flagged; on some, none.

Live filters:
  exit                  large wallets sold over the week and the 50 largest holders cut their bags
  smart money leaving   several of the most profitable wallets sold on the day, and few of them bought
  washout               price down hard on the day, with who sold and who bought

On trial, never posted: run-up, accumulation, distribution, smart money buying, a big buying or selling day. They
are logged and scored the same way, and a filter is moved up or down on its record. On the first month of data
(the median token fell about 20% a week) the three live ones went their way about 2 times in 3 and the rest were
coin flips or worse; none is a proven edge, and the record is there to find out.

Every flag is logged with the close that day, and later runs add what price did 3 and 7 days on, next to what the
median token did over the same days. That log (smart_filter_log.json in the cache) is how each filter gets judged.

Usage: smart_filter.py <cache_dir> [--replay]      prints the latest day's flags; --replay prints every day it can
"""
import contextlib, datetime as dt, heapq, json, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows_model as FM, inflow as IN, smart_money as SM, holders_model as HM, token_meta as TM

LARGE = ("whale", "big")         # holding $5K+ of the token
SMALL = ("mid", "small")


def shift(d, n):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=n)).isoformat()


def span(d, n):
    """The n days ending on d, oldest first."""
    return [shift(d, -i) for i in range(n)][::-1]


# ---------------------------------------------------------------------------------------------- facts per day
def afters(t):
    """{day: every wallet's transfers after the end of that day}, so holdings can be rebuilt as of any cached day."""
    after, out = dict(t["head"]["after"]), {}
    for d in sorted(t["days"], reverse=True):
        out[d] = after
        after = dict(after)
        mv = t["days"][d].get("moves")
        if mv:
            for a, v in mv.items():
                after[a] = int(after.get(a, 0)) + int(v)
        else:
            for a, row in t["days"][d]["rows"].items():
                after[a] = int(after.get(a, 0)) + int(row[3])
    return out


def window(t, aft, d, n):
    """The Flows page's totals for the n days ending on d, with wallets sized by what they held then. None unless
    every day of the window is cached."""
    ds = span(d, n)
    if any(x not in t["days"] for x in ds):
        return None
    return FM.token_window({**t, "head": {**t["head"], "after": aft[d]}}, ds)


def flow_facts(t, size):
    """{day: what each size group did over 1, 3 and 7 days ending that day, price over the same days, market cap}."""
    aft = afters(t)
    last = max(t["days"]); p_now = t["days"][last]["price_close"] or 0
    out = {}
    for d in sorted(t["days"]):
        p = t["days"][d]["price_close"]
        if not p:
            continue
        f = {"close": p, "cap": size * p / p_now if p_now and size else size}
        for n in (1, 3, 7):
            w = window(t, aft, d, n)
            if not w:
                continue
            f[f"L{n}"] = sum(w[g]["net"] for g in LARGE)
            f[f"R{n}"] = sum(w[g]["net"] for g in SMALL)
            f[f"P{n}"] = w["price_chg"]
            if n == 1:
                f.update(lb=sum(w[g]["buyers"] for g in LARGE), ls=sum(w[g]["sellers"] for g in LARGE),
                         whale=w["whale"]["net"], big=w["big"]["net"], all=w["all"]["net"],
                         fresh_n=w["all"]["fresh_n"], fresh=w["all"]["fresh_net"])
        out[d] = f
    return out


def top_changes(st):
    """{day: % change in the combined balance of the 50 largest holders of 7 days earlier}, the Holders page's
    "top holders" figure, for every day the state can rebuild."""
    kinds, pools = st["kinds"], set(st["pools"])
    holder = lambda a: a not in HM.BURN and a != st["token"] and a not in pools and kinds.get(a, "eoa") in HM.WALLET_KINDS
    bal, by_day = {}, {}
    for a, (b, _fi, _li, _lo, daily) in st["addrs"].items():
        if not holder(a):
            continue
        bal[a] = b
        for k, v in daily.items():
            by_day.setdefault(k, []).append((a, v))
    days = sorted(by_day)
    if not days:
        return {}
    first, asof = days[0], st["asof"]
    for k in days:                                   # walk back to the end of the day before the first movement
        for a, v in by_day[k]:
            bal[a] -= v
    tops, out, d = {}, {}, shift(first, -1)
    while d <= asof:
        for a, v in by_day.get(d, ()):
            bal[a] += v
        tops[d] = heapq.nlargest(50, ((b, a) for a, b in bal.items() if b > 0))
        was = tops.get(shift(d, -7))
        if was:
            t0 = sum(b for b, _ in was) or 1
            out[d] = (sum(max(bal.get(a, 0), 0) for _, a in was) / t0 - 1) * 100
        d = shift(d, 1)
    return out


@contextlib.contextmanager
def as_of(loaded, day):
    """Make smart_money.build() see the cache as it stood at the end of `day`."""
    orig = FM.load_token
    def view(tdir):
        t = loaded.get(os.path.basename(tdir.rstrip("/")))
        if t is None:
            t = loaded[os.path.basename(tdir.rstrip("/"))] = orig(tdir)
        days = {d: v for d, v in t["days"].items() if d <= day}
        return {**t, "head": {**t["head"], "day": max(days) if days else "0"}, "days": days}
    FM.load_token = view
    try:
        yield
    finally:
        FM.load_token = orig


def smart_facts(cache_dir, loaded, shown, day):
    """{symbol: what the most profitable wallets did on `day` and over the 7 days to it}, ranked as the Smart money
    page ranks them: only on the days before the window."""
    out = {}
    with as_of(loaded, day):
        for n in (1, 7):
            try:
                m = SM.build(cache_dir, symbols=shown, report_days=n)
            except SystemExit:
                m = None
            if not m or m["asof"] != day:
                continue
            for x in m["tokens"]:
                o = out.setdefault(x["symbol"], {})
                o[f"S{n}"] = x["smart"]["net"]; o[f"sb{n}"] = x["smart"]["buyers"]; o[f"ss{n}"] = x["smart"]["sellers"]
                if n == 1:
                    o.update(n_smart=m["n_smart"], book=x["book"]["usd"], book_n=x["book"]["wallets"], book_pl=x["book"]["pl"])
    return out


def load(cache_dir):
    """Everything the filters need, read once: tokens shown on the token pages, their flow facts and holder changes."""
    flows = FM.build(cache_dir)
    meta = json.load(open(os.path.join(cache_dir, "tokens_meta.json")))
    left = IN.passed_through(flows)
    shown = {t["symbol"] for t in flows["tokens"] if t["category"] != "stocks" and t["symbol"] not in left
             and (meta.get(t["symbol"]) or {}).get("liq", 0) >= IN.MIN_LIQ}
    loaded, F, T = {}, {}, {}
    for sym in sorted(shown):
        t = loaded[sym] = FM.load_token(os.path.join(cache_dir, sym))
        F[sym] = flow_facts(t, TM.size(meta[sym]) or 0)
        sp = os.path.join(cache_dir, "holders", f"{sym}.state.json.gz")
        T[sym] = top_changes(HM.load(sp)) if os.path.exists(sp) else {}
    return {"asof": flows["asof"], "shown": shown, "loaded": loaded, "F": F, "T": T}


# ---------------------------------------------------------------------------------------------------- the rules
from site_shell import fm, signed, pct, dshort

MIN_USD, MIN_CAP, MAX_USD = 20_000, 0.5, 100_000   # a big amount for large wallets: 0.5% of market cap, kept between these
EXIT_LARGE = 1.5                 # exit: large wallets sold this many big amounts over 7 days ...
EXIT_TOP = -8.0                  # ... and the 50 largest holders cut this % or more
SMART_USD = 10_000               # smart money leaving: they net sold this much on the day ...
SMART_EDGE = 2                   # ... with at least this many more of them selling than buying ...
SMART_MIN = 3                    # ... and at least this many selling
RUN_PCT = 20.0                   # run-up: up this % in 3 days ...
RUN_FRESH = 1.0                  # ... with new wallets buying this % of market cap that day, or large wallets a big amount
WASH_PCT = -20.0                 # washout: down this % on the day
SMALL_SIDE = 0.25                # "smaller wallets took the other side": this share of the large wallets' amount
MAX_DAY = 4                      # lines kept per day
MIN_RECORD = 5                   # a filter's record is quoted once this many of its flags have an outcome

# filter: (name, the way it is judged against the median token, days that is judged over, days before the same
# token can raise it again, live or only on trial)
KINDS = {
    "exit":     ("Exit", -1, 7, 7, True),
    "smart":    ("Smart money leaving", -1, 7, 4, True),
    "washout":  ("Washout", +1, 3, 3, True),
    "runup":    ("Run-up", -1, 7, 4, False),
    "accum":    ("Accumulation", +1, 7, 4, False),
    "distrib":  ("Distribution", -1, 7, 4, False),
    "smartbuy": ("Smart money buying", +1, 7, 4, False),
    "bigbuy":   ("Big buying day", +1, 7, 4, False),
    "bigsell":  ("Big selling day", -1, 7, 4, False),
}
ORDER = list(KINDS)


def big(cap):
    return max(MIN_USD, min(MIN_CAP / 100 * cap, MAX_USD))


def _dir(x, flat=0.5):
    return 0 if x is None or abs(x) < flat else 1 if x > 0 else -1


def find(f, top, sm):
    """Every filter one token passes on one day: {kind: (size for ranking, sentence, figures, note)}. The sentence
    is the whole story in words (the Discord line); the figures are the same story as three numbers for the page,
    each [value, what it is, -1/0/1 for down/flat/up], and the note is what the figures leave out. f: flow facts,
    top: the 50 largest holders' 7-day change, sm: smart money facts. A missing input rules its kinds out."""
    out, cap = {}, f.get("cap") or 0
    if not cap or "L1" not in f:
        return out
    b = big(cap)
    g = lambda k: f.get(k)
    L1, L3, L7, R3, R7, P1, P3, P7 = (g(k) for k in ("L1", "L3", "L7", "R3", "R7", "P1", "P3", "P7"))
    S1, sb, ss = sm.get("S1"), sm.get("sb1", 0), sm.get("ss1", 0)
    fresh_big = f["fresh"] >= RUN_FRESH / 100 * cap and f["fresh_n"] >= 20
    if L7 is not None and top is not None and L7 <= -EXIT_LARGE * b and top <= EXIT_TOP:
        s = (f"Large wallets sold {fm(L7)} in 7 days and the 50 largest holders cut {abs(top):.0f}% of their bags. "
             f"Price {pct(P7, 0)} on the week.")
        note = ""
        if R7 is not None and R7 >= max(5_000, SMALL_SIDE * abs(L7)):
            note = f"Smaller wallets bought {fm(R7)} of it."
        figs = [[signed(L7), "Large wallets, 7 days", -1], [pct(top, 0), "50 largest holders", -1], [pct(P7, 0), "Price, 7 days", _dir(P7)]]
        out["exit"] = (abs(L7) / cap + abs(top) / 100, (s + " " + note).strip(), figs, note)
    if S1 is not None and S1 <= -SMART_USD and ss >= max(SMART_MIN, sb + SMART_EDGE):
        s = f"{ss} of the top {sm.get('n_smart')} wallets by profit sold {fm(S1)} on the day; {sb or 'none'} bought."
        note = f"Out of the {sm.get('n_smart')} most profitable wallets."
        if sm.get("S7") is not None and sm["S7"] <= 1.5 * S1:
            s += f" Over 7 days they sold {fm(sm['S7'])}."
            note += f" Over 7 days they sold {fm(sm['S7'])}."
        if P1 is not None:
            s += f" Price {pct(P1, 0)} on the day."
        figs = [[signed(S1), "Smart money, on the day", -1], [f"{ss} to {sb}", "Sellers to buyers", 0],
                [pct(P1, 0) if P1 is not None else "—", "Price, on the day", _dir(P1)]]
        out["smart"] = (abs(S1) / cap, s, figs, note)
    if P3 is not None and P3 >= RUN_PCT and (fresh_big or (L3 is not None and L3 >= b)):
        s = f"{pct(P3, 0)} in 3 days."
        if L3 is not None and L3 >= b:
            s += f" Large wallets bought {fm(L3)} over those days."
        if fresh_big:
            s += f" {f['fresh_n']:,} new wallets bought {fm(f['fresh'])} on the day."
        if S1 is not None and S1 <= -SMART_USD / 2:
            s += f" Smart money sold {fm(S1)} into it."
        out["runup"] = (P3 / 100, s, None, "")
    if P1 is not None and P1 <= WASH_PCT:
        deep = P3 is not None and P3 < P1 - 5
        s = f"{pct(P1, 0)} on the day" + (f", {pct(P3, 0)} in 3 days." if deep else ".")
        if abs(L1) >= b / 2:
            s += f" Large wallets {'bought' if L1 > 0 else 'sold'} {fm(L1)}."
        if f["fresh"] >= RUN_FRESH / 200 * cap and f["fresh_n"] >= 20:
            s += f" {f['fresh_n']:,} new wallets bought {fm(f['fresh'])}."
        note = f"{pct(P3, 0)} in 3 days." if deep else ""
        if top is not None and top <= EXIT_TOP:
            s += f" The 50 largest holders have cut {abs(top):.0f}% this week."
            note = (note + f" The 50 largest holders have cut {abs(top):.0f}% this week.").strip()
        R1 = g("R1") or 0
        third = ([fm(f["fresh"]), f"Bought by {f['fresh_n']:,} new wallets", 0] if f["fresh"] >= 1_000 and f["fresh_n"] >= 5
                 else [signed(R1) if abs(R1) >= 500 else "$0", "Smaller wallets, on the day", _dir(R1, 500)])
        figs = [[pct(P1, 0), "Price, on the day", -1], [signed(L1) if abs(L1) >= 500 else "$0", "Large wallets, on the day", _dir(L1, 500)], third]
        out["washout"] = (abs(P1) / 100, s, figs, note)
    # on trial, not posted
    if L3 is not None and L3 >= b and P3 is not None and P3 <= 5 and R3 < 0:
        out["accum"] = (L3 / cap, f"Large wallets bought {fm(L3)} over 3 days while price went {pct(P3, 0)} and smaller wallets sold {fm(R3)}.", None, "")
    if L3 is not None and L3 <= -b and R3 >= SMALL_SIDE * abs(L3):
        out["distrib"] = (abs(L3) / cap, f"Large wallets sold {fm(L3)} over 3 days while smaller wallets bought {fm(R3)}.", None, "")
    if S1 is not None and S1 >= SMART_USD and sb >= max(SMART_MIN, ss + SMART_EDGE):
        out["smartbuy"] = (S1 / cap, f"{sb} of the top {sm.get('n_smart')} wallets by profit bought {fm(S1)} on the day; {ss or 'none'} sold.", None, "")
    if abs(L1) >= 2 * b:
        out["bigbuy" if L1 > 0 else "bigsell"] = (abs(L1) / cap, f"Large wallets {'bought' if L1 > 0 else 'sold'} {fm(L1)} on the day.", None, "")
    return out


# ------------------------------------------------------------------------------------------------------ the log
def _gap(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def add_day(log, D, day, smart):
    """Work out one day's flags and append them to the log. A token raises a kind again only after that kind's
    quiet days have passed. Of the posted kinds each token gets one line (the first kind in ORDER it shows, the
    others noted after it), and the day keeps MAX_DAY lines, strongest kinds first."""
    last = {}
    for it in log["items"]:
        last[(it["s"], it["kind"])] = it["d"]
    lines = []
    for sym in sorted(D["shown"]):
        f = D["F"][sym].get(day)
        if not f:
            continue
        got = find(f, D["T"][sym].get(day), smart.get(sym, {}))
        fired = [k for k in ORDER if k in got and ((sym, k) not in last or _gap(last[(sym, k)], day) >= KINDS[k][3])]
        posted = [k for k in fired if KINDS[k][4]]
        for k in fired:
            if not KINDS[k][4]:
                log["items"].append({"d": day, "s": sym, "kind": k, "posted": False, "text": got[k][1], "close": f["close"]})
        if posted:
            k = posted[0]
            also = [KINDS[x][0].lower() for x in ORDER if x in got and KINDS[x][4] and x != k]
            lines.append((ORDER.index(k), -got[k][0], {"d": day, "s": sym, "kind": k, "posted": True, "text": got[k][1],
                                                         "figs": got[k][2], "note": got[k][3], "also": also,
                                                         "close": f["close"], "kinds": posted}))
    lines.sort(key=lambda x: x[:2])
    for i, (_, _, it) in enumerate(lines):
        it["posted"] = i < MAX_DAY                         # past the day's limit: logged, scored and named, without a line
        if not it["posted"]:
            it["cut"] = True
        kinds = it["kinds"]
        log["items"].append(it)
        for k in kinds[1:]:                                # its other kinds fired too: logged for their own record
            log["items"].append({"d": day, "s": it["s"], "kind": k, "posted": False, "with": it["kind"], "close": it["close"],
                                 "text": ""})
    log["days"].append(day)


def outcomes(log, D):
    """Fill in what price did 3 and 7 days after each logged flag, and what the median token did over the same days."""
    closes = {s: {d: f["close"] for d, f in D["F"][s].items()} for s in D["shown"]}
    med = {}
    def move(sym, d, n):
        c = closes.get(sym, {})
        a, b = c.get(d), c.get(shift(d, n))
        return (b / a - 1) * 100 if a and b else None
    for it in log["items"]:
        for n in (3, 7):
            if it.get(f"r{n}") is not None:
                continue
            r = move(it["s"], it["d"], n)
            if r is None:
                continue
            if (it["d"], n) not in med:
                xs = [x for x in (move(s, it["d"], n) for s in closes) if x is not None]
                med[(it["d"], n)] = statistics.median(xs) if len(xs) >= 5 else None
            if med[(it["d"], n)] is not None:
                it[f"r{n}"] = round(r, 1); it[f"m{n}"] = round(med[(it["d"], n)], 1)


def record(log, upto=None):
    """{kind: {"n": flags with an outcome, "hit": how many went the way the filter is judged, "r": their average move,
    "m": the median token's average move over the same days, "days": the horizon}}, using only flags whose
    outcome was known by `upto` (so a day's line never quotes its own future)."""
    out = {}
    for k, (_name, side, n, _q, _p) in KINDS.items():
        xs = [it for it in log["items"] if it["kind"] == k and it.get(f"r{n}") is not None
              and (upto is None or shift(it["d"], n) <= upto)]
        out[k] = {"n": len(xs), "hit": sum(1 for it in xs if (it[f"r{n}"] - it[f"m{n}"]) * side > 0),
                  "r": round(statistics.mean(it[f"r{n}"] for it in xs), 1) if xs else None,
                  "m": round(statistics.mean(it[f"m{n}"] for it in xs), 1) if xs else None, "days": n,
                  "fired": sum(1 for it in log["items"] if it["kind"] == k)}
    return out


def update(cache_dir, log_path=None, replay=True):
    """Bring the log up to the latest cached day and return (log, data). The first run replays every cached day it
    has enough history for, so the record starts with a few weeks instead of nothing."""
    log_path = log_path or os.path.join(cache_dir, "smart_filter_log.json")
    D = load(cache_dir)
    log = json.load(open(log_path)) if os.path.exists(log_path) else {"days": [], "items": []}
    all_days = sorted({d for s in D["shown"] for d in D["F"][s]})
    if log["days"]:
        todo = [d for d in all_days if d > max(log["days"])]         # normally one day; more after missed runs
    else:
        todo = all_days if replay else [D["asof"]]
    for d in todo:
        add_day(log, D, d, smart_facts(cache_dir, D["loaded"], D["shown"], d))
    outcomes(log, D)
    log["asof"] = D["asof"]
    tmp = log_path + ".tmp"
    json.dump(log, open(tmp, "w"), separators=(",", ":")); os.replace(tmp, log_path)
    return log, D


def hits(rec, k):
    """'5 of 6 did worse than the median token over the next 7 days', or '' while the record is too short."""
    r = rec[k]
    if r["n"] < MIN_RECORD:
        return ""
    return (f"{r['hit']} of {r['n']} did {'better' if KINDS[k][1] > 0 else 'worse'} than the median token "
            f"over the next {r['days']} days")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    log, D = update(sys.argv[1], log_path=(sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv else None))
    rec = record(log)
    days = log["days"] if "--replay" in sys.argv else [log["asof"]]
    for d in days:
        its = [it for it in log["items"] if it["d"] == d and it["posted"]]
        print(f"\n{d}  ({len(its)} posted)")
        for it in its:
            n = KINDS[it["kind"]][2]
            after = (f"  -> {n}d {it[f'r{n}']:+.0f}% (median token {it[f'm{n}']:+.0f}%)" if it.get(f"r{n}") is not None else "")
            print(f"  {it['s']:9} {KINDS[it['kind']][0]}: {it['text']}" + (f" Also: {', '.join(it['also'])}." if it["also"] else "") + after)
    print("\nrecord:")
    for k in ORDER:
        r = rec[k]
        print(f"  {KINDS[k][0]:22} {'live    ' if KINDS[k][4] else 'on trial'}  fired {r['fired']:3}  with outcome {r['n']:3}  "
              f"went its way {r['hit']:3}  avg move {r['r']} vs median token {r['m']} over {r['days']}d")
