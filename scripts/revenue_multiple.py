"""Revenue multiple: what the market pays for each RH-native project's revenue, for projects with a token on
Robinhood Chain. Fees, revenue and holder revenue come from DefiLlama (Robinhood Chain only); price, fully diluted
value, market cap and liquidity from GeckoTerminal. RH-native means at least half of the project's revenue across all
chains is earned on Robinhood Chain, which keeps multichain apps out.

  multiple      = fully diluted value / (revenue over the last 30 days x 365/30)
  holder yield  = (holder revenue over the last 30 days x 365/30) / fully diluted value
                  holder revenue is DefiLlama's: buybacks, burns and payouts to stakers or holders
  pace          = last 7 days of revenue vs the 30-day average week (-40% = the last week ran 40% below the month)

Usage: revenue_multiple.py <out.json> [--cache DIR]   (with --cache, reads/writes the raw API responses there)
"""
import json, os, sys, time, urllib.error, urllib.request

LLAMA = "https://api.llama.fi"
GT = "https://api.geckoterminal.com/api/v2/networks/robinhood/tokens/"
NATIVE_SHARE = 0.5                       # RH-native: at least half the project's revenue is earned on Robinhood Chain
MIN_REV30 = 10_000                       # skip GeckoTerminal lookups for projects too small to show
THIN_LIQ = 50_000
SMALL_FLOAT = 0.25
CHECK_BELOW = 1.0
YEAR = 365 / 30


def get(url, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "sob-valuation"})
            return json.load(urllib.request.urlopen(req, timeout=90))
        except urllib.error.HTTPError as e:
            if e.code == 404 or k == tries - 1:
                raise
            time.sleep(15 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(15 * (k + 1))


def cached(cache, name, url):
    p = os.path.join(cache, name) if cache else None
    if p and os.path.exists(p):
        return json.load(open(p))
    d = get(url)
    if p:
        json.dump(d, open(p, "w"))
    return d


def build(cache=None, overrides=None):
    q = "excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
    fees = cached(cache, "dailyFees.json", f"{LLAMA}/overview/fees/Robinhood?{q}&dataType=dailyFees")["protocols"]
    rev = {p["defillamaId"]: p for p in cached(cache, "dailyRevenue.json", f"{LLAMA}/overview/fees/Robinhood?{q}&dataType=dailyRevenue")["protocols"]}
    hrev = {p["defillamaId"]: p for p in cached(cache, "dailyHoldersRevenue.json", f"{LLAMA}/overview/fees/Robinhood?{q}&dataType=dailyHoldersRevenue")["protocols"]}
    glob_rev = {p["defillamaId"]: p for p in cached(cache, "globalRevenue.json", f"{LLAMA}/overview/fees?{q}&dataType=dailyRevenue")["protocols"]}
    protos = cached(cache, "protocols.json", f"{LLAMA}/protocols")
    tok = {}
    for p in protos:
        a = (p.get("address") or "").lower()
        if a.startswith("robinhood:0x"):
            for key in (p.get("parentProtocol"), "slug:" + (p.get("slug") or "")):
                if key:
                    tok[key] = (p.get("symbol"), a.split(":")[1])
    groups = {}
    for p in fees:
        if p.get("category") == "Chain":
            continue
        groups.setdefault(p.get("parentProtocol") or "slug:" + p["slug"], []).append(p)
    rows = []
    for key, ch in groups.items():
        names = [p["name"] for p in ch]
        words = names[0].split()
        for n in names[1:]:
            w = n.split(); j = 0
            while j < min(len(words), len(w)) and words[j] == w[j]:
                j += 1
            words = words[:j]
        name = " ".join(words) or names[0]
        t = (overrides or {}).get(name)
        t = (None, t) if t else tok.get(key) or next((tok["slug:" + p["slug"]] for p in ch if "slug:" + p["slug"] in tok), None)
        if not t:
            continue
        s = lambda src, k: sum((src.get(p["defillamaId"]) or {}).get(k) or 0 for p in ch)
        f = {p["defillamaId"]: p for p in ch}
        r = {"name": name, "category": ch[0].get("category"), "token": t[1],
             "fees30": s(f, "total30d"), "fees_prev30": s(f, "total60dto30d"), "fees7": s(f, "total7d"),
             "rev30": s(rev, "total30d"), "rev7": s(rev, "total7d"), "hold30": s(hrev, "total30d"),
             "double_counted": any("double count" in json.dumps((rev.get(p["defillamaId"]) or {}).get("methodology") or "").lower() for p in ch),
             "slugs": [p["slug"] for p in ch]}
        glob30 = s(glob_rev, "total30d")
        r["rh_share"] = r["rev30"] / glob30 if glob30 else 1.0
        if r["rev30"] < MIN_REV30 or r["rh_share"] < NATIVE_SHARE:
            continue                         # too small, or mostly earned on other chains (multichain apps)
        r["llama"] = "https://defillama.com/protocol/" + (key.split("#", 1)[1] if key.startswith("parent#") else ch[0]["slug"])
        rows.append(r)
    priced = []
    for r in rows:
        try:
            d = cached(cache, f"gt_{r['token']}.json", GT + r["token"])["data"]["attributes"]
        except urllib.error.HTTPError:
            continue                     # token not listed on GeckoTerminal: no price, no multiple
        priced.append(r)
        time.sleep(0 if cache and os.path.exists(os.path.join(cache, f"gt_{r['token']}.json")) else 2.5)
        r.update({"symbol": d["symbol"], "price": float(d["price_usd"] or 0), "fdv": float(d["fdv_usd"] or 0),
                  "mcap": float(d["market_cap_usd"]) if d.get("market_cap_usd") else None,
                  "liq": float(d["total_reserve_in_usd"] or 0), "vol24": float((d.get("volume_usd") or {}).get("h24") or 0)})
        ann = r["rev30"] * YEAR
        r["multiple"] = r["fdv"] / ann if ann and r["fdv"] else None
        r["holder_yield"] = r["hold30"] * YEAR / r["fdv"] * 100 if r["fdv"] and r["hold30"] else None
        r["pace"] = (r["rev7"] / (r["rev30"] * 7 / 30) - 1) * 100 if r["rev30"] else None
        r["fees_chg"] = (r["fees30"] / r["fees_prev30"] - 1) * 100 if r["fees_prev30"] else None
        flags = []
        if r["liq"] < THIN_LIQ:
            flags.append("thin liquidity")
        if r["mcap"] and r["fdv"] and r["mcap"] / r["fdv"] < SMALL_FLOAT:
            flags.append("small float")
        if r["double_counted"]:
            flags.append("double counted")
        if r["multiple"] is not None and r["multiple"] < CHECK_BELOW:
            flags.append("check")
        r["flags"] = flags
    rows = [r for r in priced if r["multiple"] is not None]
    rows.sort(key=lambda r: r["multiple"])
    return {"rows": rows}


if __name__ == "__main__":
    out = sys.argv[1]
    cache = sys.argv[sys.argv.index("--cache") + 1] if "--cache" in sys.argv else None
    uni = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "universe.json")
    ov = {}
    if os.path.exists(uni):
        u = {t["symbol"]: t["address"].lower() for t in json.load(open(uni))}
        if "STANDARD" in u:
            ov["The Standard Reserve"] = u["STANDARD"]
    m = build(cache, ov)
    json.dump(m, open(out, "w"), indent=1)
    for r in m["rows"]:
        print(f"{r['name'][:22]:22s} {r['symbol']:11s} fdv {r['fdv']:>12,.0f} rev30 {r['rev30']:>10,.0f} x{r['multiple']:.2f} "
              f"yield {('%.0f%%' % r['holder_yield']) if r['holder_yield'] else '—':>6} pace {r['pace']:+.0f}% fees {r['fees_chg'] if r['fees_chg'] is None else round(r['fees_chg'])}% {r['flags']}")
