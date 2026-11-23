"""The home page: Robinhood Chain at a glance, built around one question. Is money coming in or leaving?

  the answer        one sentence and the inflow score (inflow.py), with its three parts shown
  net capital flow  stablecoins and ETH arriving less leaving, per day (capital.py)
  four numbers      capital on the chain, TVL, DEX volume, fees
  where it went     the RH tokens wallets bought and sold most this week (flows cache)

Usage: build_home.py <cache_dir> <out.html>      cache_dir is data/flows_cache: it holds capital.json,
       tokens_meta.json, the token day files and holders/ states. Writes a full page; `view()` gives the parts.
"""
import datetime as dt, gzip, html, json, math, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows_model as FM
import inflow as IN
import site_shell as SH
import token_meta as TM
from site_shell import fm, signed, pct, dshort

FLAT = 0.5          # 7-day flow within this % of the capital on the chain reads as steady
TOP = 5
AGO = 7             # the level also marks where the score read this many days earlier


def shown_tokens(flows, meta):
    """Crypto tokens with enough liquidity to mean something, whose volume isn't passed through throwaway wallets.
    Stock tokens have their own page."""
    out = IN.passed_through(flows)
    return [t for t in flows["tokens"] if t["category"] != "stocks" and t["symbol"] not in out
            and (meta.get(t["symbol"]) or {}).get("liq", 0) >= IN.MIN_LIQ]


def token_index(states_dir, symbols):
    """Median daily move of the tracked tokens, chained into an index (100 at the start)."""
    closes = {}
    for s in symbols:
        p = os.path.join(states_dir, f"{s}.state.json.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                closes[s] = json.load(f)["closes"]
    days = sorted({d for c in closes.values() for d in c})
    idx, lvl, prev = {}, 100.0, None
    for d in days:
        if prev:
            rs = [math.log(c[d] / c[prev]) for c in closes.values() if c.get(d) and c.get(prev)]
            if len(rs) >= 5:
                lvl *= math.exp(statistics.median(rs))
        idx[d] = lvl; prev = d
    return idx


def followed(rows, idx, min_weeks=4):
    """What the median RH token did the week after a week of net inflow, and after a week of net outflow."""
    if not idx:
        return None
    ds = [r["d"] for r in rows]; net = {r["d"]: r["net"] for r in rows}
    last = max(idx)
    pos, neg, first = [], [], None
    i = len(ds) - 1
    while i >= 0 and ds[i] > last:
        i -= 1
    i -= 7                                            # the latest week has no "week after" yet
    while i - 6 >= 0:
        a, b = ds[i], ds[i + 7]
        if a in idx and b in idx and idx[a] > 0:
            f = sum(net[ds[j]] for j in range(i - 6, i + 1))
            (pos if f > 0 else neg).append((idx[b] / idx[a] - 1) * 100)
            first = ds[i - 6]
        i -= 7
    if len(pos) < min_weeks or len(neg) < min_weeks:
        return None
    return {"in": statistics.median(pos), "out": statistics.median(neg), "n_in": len(pos), "n_out": len(neg), "from": first}


def score_ago(cache_dir, flows, toks, rows, states, parts, ago=AGO):
    """The inflow score as it read `ago` days earlier: the same three parts, over the 7 days ending then, for the
    tokens shown today. None unless every part today's score uses can be rebuilt for that day (the flows cache has
    to reach back ago + 7 days), so the two readings are always like for like."""
    then = {"capital": IN.capital_part(rows, len(rows) - 1 - ago), "buying": None, "wallets": None}
    A = dt.date.fromisoformat(flows["asof"]) - dt.timedelta(days=ago)
    days = [(A - dt.timedelta(days=i)).isoformat() for i in range(7)][::-1]
    want, win = {t["symbol"] for t in toks}, []
    for name in sorted(os.listdir(cache_dir)):
        tdir = os.path.join(cache_dir, name)
        if not (os.path.isdir(tdir) and os.path.exists(os.path.join(tdir, "head.json.gz"))):
            continue
        raw = FM.load_token(tdir)
        if raw["symbol"] in want and all(d in raw["days"] for d in days):
            # token_window sizes wallets by their holdings at the head, so only its all-wallet totals hold for an
            # earlier window; those are all the buying part needs
            win.append({"w": {"7d": FM.token_window(raw, days)}})
    if want and len(win) >= 0.8 * len(want):
        then["buying"] = IN.buying_part(win)
    if os.path.isdir(states):
        nw = IN.new_wallets(states, sorted(want), flows["asof"], days=35 + ago)
        ds = sorted(nw)[:35]
        then["wallets"] = IN.wallets_part({d: nw[d] for d in ds}) if len(ds) == 35 else None
    if any((parts.get(k) is None) != (then[k] is None) for k in then):
        return None
    return IN.combine(then)


def model(cache_dir):
    cap = json.load(open(os.path.join(cache_dir, "capital.json")))
    meta = json.load(open(os.path.join(cache_dir, "tokens_meta.json")))
    flows = FM.build(cache_dir)
    toks = shown_tokens(flows, meta)
    rows = cap["rows"]
    states = os.path.join(cache_dir, "holders")
    syms = [t["symbol"] for t in toks]
    nw = IN.new_wallets(states, syms, flows["asof"]) if os.path.isdir(states) else {}
    parts = {"capital": IN.capital_part(rows), "buying": IN.buying_part(toks) if "7d" in flows["windows"] else None,
             "wallets": IN.wallets_part(nw) if nw else None}
    score = IN.combine(parts)
    try:
        ago = score_ago(cache_dir, flows, toks, rows, states, parts) if score is not None else None
    except Exception as e:                    # the marker is an extra: never lose the page over it
        print(f"home: no earlier reading ({e})"); ago = None
    idx = token_index(states, syms) if os.path.isdir(states) else {}
    prev = IN.capital_part(rows, len(rows) - 8)
    wid = "7d" if "7d" in flows["windows"] else "1d"
    ranked = sorted(toks, key=lambda t: -t["w"][wid]["all"]["net"])
    tok_row = lambda t: {"s": t["symbol"], "net": t["w"][wid]["all"]["net"], "chg": t["w"][wid]["price_chg"],
                         "of": t["w"][wid]["all"]["net"] / TM.size(meta[t["symbol"]]) * 100 if TM.size(meta[t["symbol"]]) else None}
    return {"asof": cap["asof"], "flows_asof": flows["asof"], "rows": rows, "parts": parts, "score": score, "prev": prev,
            "score_ago": ago,
            "new_wallets": nw, "followed": followed(rows, idx), "wid": wid, "window": flows["windows"][wid],
            "bought": [tok_row(t) for t in ranked[:TOP] if t["w"][wid]["all"]["net"] > 1],
            "sold": [tok_row(t) for t in ranked[::-1][:TOP] if t["w"][wid]["all"]["net"] < -1],
            "n_tokens": len(toks), "meta": meta,
            "index7": (idx[max(idx)] / idx[sorted(idx)[-8]] - 1) * 100 if len(idx) > 8 else None}


# ---------- words ----------
def headline(m):
    """The site's headline form: the chain's name, highlighted, then what its capital is doing."""
    c = m["parts"]["capital"]
    tail = ("at a glance." if not c else "is drawing capital." if c["value"] > FLAT
            else "is losing capital." if c["value"] < -FLAT else "is holding steady.")
    return f'<span class="hl" style="white-space:nowrap">Robinhood Chain</span><br>{tail}'


def headline_text(m):
    c = m["parts"]["capital"]
    return "Robinhood Chain " + ("at a glance." if not c else "is drawing capital." if c["value"] > FLAT
                                 else "is losing capital." if c["value"] < -FLAT else "is holding steady.")


def lede(m):
    """Two short sentences at most, every number from the model."""
    c, b, w = m["parts"]["capital"], m["parts"]["buying"], m["parts"]["wallets"]
    out = []
    if c:
        s = f"<b>{fm(c['net'])}</b> net {'came in' if c['net'] >= 0 else 'left'} in 7 days"
        st, eth = c["stables"], c["eth"]
        if (st >= 0) == (eth >= 0) == (c["net"] >= 0):
            big, name = (st, "stablecoins") if abs(st) >= abs(eth) else (eth, "ETH")
            s += f", {fm(big)} of it {name}."
        else:
            s += f": stablecoins {signed(st)}, ETH {signed(eth)}."
        out.append(s)
    if b:
        s = f"Wallets net {'bought' if b['net'] >= 0 else 'sold'} <b>{fm(b['net'])}</b> of RH tokens"
        if w:
            s += f", and new wallets ran {abs(w['value']):.0f}% {'above' if w['value'] >= 0 else 'below'} a typical week"
        out.append(s + ".")
    return " ".join(out)


# Each part of the score as a card: two points and its score. How it is scored sits behind the "i".
POINTS = {
    "capital": lambda p: (signed(p["net"]), "in 7 days", f"{abs(p['value']):.1f}% of the {fm(p['stock'])} on chain"),
    "buying": lambda p: (f"{p['value']:.1f}%", "of volume was buying", f"{signed(p['net'])} net in 7 days"),
    "wallets": lambda p: (f"{p['week']:,.0f}", "this week", f"{abs(p['value']):.0f}% {'more' if p['value'] >= 0 else 'fewer'} than a typical week"),
}
ABOUT = {
    "capital": "7-day net flow of stablecoins and ETH, as a share of the capital already on the chain. −3% scores 0, flat scores 50, +3% scores 100.",
    "buying": "The share of wallet volume in RH tokens that was buying, bots left out. 45% scores 0, 50% scores 50, 55% scores 100.",
    "wallets": "Wallets taking a first position of $10 or more this week, against a typical week this month. −50% scores 0, level scores 50, +50% scores 100.",
}


def _info(key, text):
    return (f'<button type="button" class="info" aria-label="How this is measured" aria-expanded="false" aria-controls="pop-{key}">i</button>',
            f'<div class="pop" id="pop-{key}" role="note">{text}</div>')


def _tone(score):
    return 0 if score >= IN.IN else 1 if score <= IN.OUT else 2


def meter(m):
    """The inflow score as an instrument: the figure, its state, and a level drawn in the site's ASCII."""
    if m["score"] is None:
        return ""
    sc = round(m["score"]); state = IN.state(m["score"]); tone = _tone(m["score"])
    ago = m.get("score_ago")
    was = "" if ago is None else f' data-was="{round(ago)}"'
    said = "" if ago is None else f". It read {round(ago)} a week ago"
    return f"""<div class="meter" role="img" aria-label="Inflow score {sc} out of 100: {state.lower()}{said}">
<div class="m-n"><div class="k">Inflow score</div><div class="m-v"><b class="num">{sc}</b><small>/100</small></div>
<div class="m-s">{SH.SYM[("up", "down", "flat")[tone]]}{state}</div></div>
<div class="m-art{'' if ago is None else ' has-was'}" aria-hidden="true" data-score="{sc}"{was}><pre class="cur"></pre><pre class="shp"></pre><pre class="lab"></pre><pre class="was"></pre></div>
</div>"""


def cells(m):
    """Title, two points, score. Nothing else on the card; the weight and the scale are behind the "i"."""
    out = ""
    for k in ("capital", "buying", "wallets"):
        p = m["parts"].get(k)
        if not p:
            continue
        fig, unit, second = POINTS[k](p)
        btn, pop = _info(k, f"{round(IN.WEIGHTS[k] * 100)}% of the inflow score. {ABOUT[k]}")
        out += (f'<div class="cell"><div class="k">{IN.LABELS[k]}{btn}</div>'
                f'<div class="p1"><b class="num">{fig}</b> {unit}</div><div class="p2">{second}</div>'
                f'<div class="sc">Score: <b>{round(p["score"])}</b>/100</div>{pop}</div>')
    f = m["followed"]
    if f:
        mv = lambda x: "Flat" if abs(x) < 0.5 else f"{'+' if x > 0 else SH.MINUS}{abs(x):.0f}%"
        now_out = m["parts"]["capital"] and m["parts"]["capital"]["net"] < 0
        a, b = (("out", "outflow"), ("in", "inflow")) if now_out else (("in", "inflow"), ("out", "outflow"))
        btn, pop = _info("after", f"What the median RH token did in the week after each kind of week since {dshort(f['from'])}: "
                                  f"{f['n_out']} weeks of net outflow, {f['n_in']} of net inflow. A short record, not a rule.")
        out += (f'<div class="cell"><div class="k">The week after an {a[1]} week{btn}</div>'
                f'<div class="p1"><b class="num">{mv(f[a[0]])}</b> median RH token</div>'
                f'<div class="p2">{mv(f[b[0]])} after {b[1]} weeks</div>'
                f'<div class="sc">{f["n_" + a[0]]} such weeks since {dshort(f["from"])}</div>{pop}</div>')
    return out


def _chg(now, before):
    return (now / before - 1) * 100 if before else None


def tiles(m):
    R = m["rows"]
    stock = lambda r: r["stables_stock"] + (r["eth_units"] or 0) * (r["eth_px"] or 0)
    last = R[-1]
    cap_now = stock(last)
    tv = [(r["d"], r["tvl"]) for r in R if r.get("tvl")]
    roll = lambda key: [sum(x[key] or 0 for x in R[i - 6:i + 1]) for i in range(6, len(R))]
    vol7, fee7 = roll("vol"), roll("fees")
    out = [("Capital on the chain", fm(cap_now),
            f"Stablecoins {fm(last['stables_stock'])} · ETH {fm((last['eth_units'] or 0) * (last['eth_px'] or 0))}", None,
            [stock(r) for r in R[-60:]])]
    if len(tv) > 8:
        out.append(("Total value locked", fm(tv[-1][1]), "vs a week ago", _chg(tv[-1][1], tv[-8][1]), [x[1] for x in tv[-60:]]))
    if len(vol7) > 8:
        out.append(("DEX volume, 7 days", fm(vol7[-1]), "vs the week before", _chg(vol7[-1], vol7[-8]), vol7[-60:]))
    if len(fee7) > 8:
        out.append(("Fees paid, 7 days", fm(fee7[-1]), "vs the week before", _chg(fee7[-1], fee7[-8]), fee7[-60:]))
    h = ""
    for label, value, sub, chg, series in out:
        d = ""
        if chg is not None:
            d = f'<span class="{"up" if chg >= 0 else "dn"}">{"▲" if chg >= 0 else "▼"}</span> {abs(chg):.1f}% '
        h += (f'<div class="tile"><div class="t-l">{label}</div><div class="t-v num">{value}</div>'
              f'<div class="t-s">{d}{sub}</div>{SH.spark(series)}</div>')
    return f'<div class="tiles">{h}</div>'


def token_list(items, meta, down, href):
    if not items:
        return f'<p class="empty">No token was net {"sold" if down else "bought"} in this window.</p>'
    top = max(abs(x["net"]) for x in items) or 1
    li = ""
    for i, x in enumerate(items, 1):
        name = (meta.get(x["s"]) or {}).get("name") or x["s"]
        of = f"<small>{abs(x['of']):.2f}% of market cap</small>" if x.get("of") is not None else ""
        li += (f'<li><a class="row" href="{href}"><span class="n">{i}</span>{SH.avatar(x["s"], meta)}'
               f'<span><span class="tks">{html.escape(x["s"])}</span><span class="sub">{html.escape(name)}</span></span>'
               f'<span class="bar{" dn" if down else ""}"><i style="width:{max(abs(x["net"]) / top * 100, 2):.0f}%"></i></span>'
               f'<span class="val">{signed(x["net"])}{of}</span></a></li>')
    return f'<ol class="rank">{li}</ol>'


def where(m, href):
    """Two lists side by side; when one side is empty, the other takes the full width."""
    b, d = m["bought"], m["sold"]
    panel = lambda title, items, down: f'<div class="panel"><h3 class="lh">{title}</h3>{token_list(items, m["meta"], down, href("flows"))}</div>'
    if b and d:
        return f'<div class="grid2 top">{panel("Most bought", b, False)}{panel("Most sold", d, True)}</div>'
    if not b and not d:
        return '<p class="empty">No token flows for this window yet.</p>'
    note = f'<p class="note" style="margin-top:12px">No token was net {"bought" if d else "sold"} over the week.</p>'
    return f'<div class="top one">{panel("Most sold" if d else "Most bought", d or b, bool(d))}</div>{note}'


def view(m, href=lambda i: f"/{i}"):
    R = m["rows"]
    data = {"rows": [[r["d"], r["net"], r["stables"], r["eth"] or 0] for r in R]}
    w = m["window"]
    span = dshort(w["to"]) if m["wid"] == "1d" else f"{dshort(w['from'])} to {dshort(w['to'])}"
    last14 = "".join(f'<tr><td>{dshort(r["d"])}</td><td>{signed(r["net"])}</td><td>{signed(r["stables"])}</td><td>{signed(r["eth"] or 0)}</td></tr>'
                     for r in R[-14:][::-1])
    idx7 = f' The median RH token moved {pct(m["index7"], 0)} over the week.' if m.get("index7") is not None else ""
    speed = 0.45 + 0.55 * min(1.0, abs((m["score"] if m["score"] is not None else 50) - 50) / 50)
    body = f"""
<section class="herocard" aria-label="Overview">
<div class="art" aria-hidden="true" data-speed="{speed:.2f}"><pre class="cur"></pre><pre class="shp"></pre></div>
<div class="hc"><p class="kicker">State of Robinhood Chain · 7 days to {dshort(m['asof'])} <span class="nb">· updated daily</span></p>
<h1>{headline(m)}</h1>
<p class="lede">{lede(m)}</p></div>
<div class="lower">{meter(m)}<div class="strip">{cells(m)}</div></div>
</section>

<section class="sec">
<div class="sec-h"><div><h2>Net capital flow</h2><p>Stablecoins and ETH arriving on the chain, less what left. Per day.</p></div>
<div class="seg" id="home-range" role="group" aria-label="Range"></div></div>
<div class="panel chartp"><div class="chart" id="home-chart"></div>
<div class="legend"><span><i class="k-in"></i>In</span><span><i class="k-out"></i>Out</span><span><i class="k-avg"></i>7-day average</span></div></div>
<details class="figs"><summary>Daily figures</summary><div class="scroll"><table class="tbl"><tr><th>Day</th><th>Net flow</th><th>Stablecoins</th><th>ETH</th></tr>{last14}</table></div></details>
</section>

<section class="sec">{tiles(m)}</section>

<section class="sec">
<div class="sec-h"><div><h2>Where it went</h2><p>What wallets bought and sold across {m['n_tokens']} RH tokens, {span}.{idx7}</p></div>
<a class="more" href="{href('flows')}">All token flows →</a></div>
{where(m, href)}
</section>

<details class="how"><summary>How this is measured</summary><dl>
<dt>Net capital flow</dt><dd>Stablecoins (USDG, USDe, syrupUSDG) minted on Robinhood Chain less burned, read from the chain, plus the change in ETH held by the chain's bridge on Ethereum, valued at that day's ETH price. Every ETH on the chain is backed by ETH in that bridge, whichever bridge a user took.</dd>
<dt>Inflow score</dt><dd>0 to 100 on fixed scales, 50 is balanced. Capital onto the chain (40%): 7-day net flow as a share of capital already there, −3% scores 0 and +3% scores 100. Buying of RH tokens (35%): the share of wallet volume that was buying, 45% scores 0 and 55% scores 100. New wallets (25%): first positions of $10+ this week against a typical week this month, −50% scores 0 and +50% scores 100. It is rescored every day over the last 7 days; the fainter level shows where it read a week earlier.</dd>
<dt>Where it went</dt><dd>USD bought less USD sold by wallets in each token's main pools. Bots are left out, and so are tokens with under $50K of liquidity or with {IN.PASS_NOTE}. Stock tokens have their own page.</dd>
<dt>Sources</dt><dd>Robinhood Chain (stablecoins, trades, holders), DefiLlama (bridge ETH, TVL, volume, fees), GeckoTerminal (prices).</dd>
</dl></details>
"""
    return {"id": "home", "title": "Overview", "desc": "Robinhood Chain at a glance: is money coming in or leaving?",
            "body": body, "css": CSS, "js": JS, "data": data}


CSS = r"""
/* the hero card: words on the left, the chain drawn in ASCII on the right, the score and its parts below */
.herocard{position:relative;border:1px solid var(--line);border-radius:14px;background:var(--panel)}
.art{position:absolute;left:0;right:0;top:0;bottom:var(--lowerh,260px);overflow:hidden;border-radius:14px 14px 0 0;-webkit-mask-image:linear-gradient(90deg,transparent 0,transparent 36%,#000 64%);mask-image:linear-gradient(90deg,transparent 0,transparent 36%,#000 64%)}
.art pre,.m-art pre{position:absolute;inset:0;margin:0;font:11px/13px var(--mono);white-space:pre;overflow:hidden;letter-spacing:0}
.art .cur{color:var(--soft2);opacity:.55}
.m-art .cur{color:var(--soft2);opacity:.6}
.m-art .lab{color:var(--soft)}
.m-art .was{color:var(--lime-ink);opacity:.34}
.art .shp,.m-art .shp{color:var(--lime-ink)}
.hc{position:relative;z-index:1;padding:48px 48px 44px;max-width:60%}
.hc .lede{max-width:52ch}
.hl{background-image:linear-gradient(100deg,transparent 42%,rgba(255,255,255,.65) 50%,transparent 58%),linear-gradient(var(--lime),var(--lime));
 background-repeat:no-repeat;background-size:260% 100%,0% 100%;background-position:150% 0,0 0;color:inherit;padding:0 .1em;margin:0 -.04em;border-radius:.06em;
 -webkit-box-decoration-break:clone;box-decoration-break:clone;animation:hl 1s cubic-bezier(.65,0,.35,1) .35s forwards,sheen2 6s ease-in-out 2.2s infinite}
@keyframes hl{to{background-size:260% 100%,100% 100%;color:#151514}}
@keyframes sheen2{0%{background-position:150% 0,0 0}22%,100%{background-position:-60% 0,0 0}}
@media (prefers-reduced-motion:reduce){.hl{animation:none!important;background-size:260% 100%,100% 100%;color:#151514}}
.lower{position:relative;z-index:1}
.meter{display:grid;grid-template-columns:230px minmax(0,1fr);align-items:center;gap:28px;padding:22px 48px 20px 26px;border-top:1px solid var(--line)}
.k{font-size:13px;color:var(--soft)}
.m-v{display:flex;align-items:baseline;gap:6px;margin-top:2px}
.m-v b{font:600 64px/1 var(--tight);letter-spacing:-.04em}
.m-v small{font:500 17px var(--tight);color:var(--soft2)}
.m-s{display:flex;align-items:center;gap:8px;margin-top:8px;font:600 19px var(--tight);letter-spacing:-.01em}
.m-art{position:relative;height:91px;min-width:0}
.m-art.has-was{height:104px}
.strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid var(--line)}
.cell{position:relative;padding:22px 26px 22px;border-left:1px solid var(--line);min-width:0}
.cell:first-child{border-left:0}
.cell .k{display:flex;align-items:center;gap:7px}
.p1{margin-top:8px;color:var(--soft);font-size:13.5px}
.p1 b{margin-right:5px;color:var(--text);font:600 25px/1.15 var(--tight);letter-spacing:-.015em;white-space:nowrap}
.p2{margin-top:3px;color:var(--soft);font-size:13.5px}
.sc{margin-top:12px;color:var(--soft);font-size:13.5px}
.sc b{color:var(--text);font-weight:600}
.chartp{padding:20px 20px 14px}
.chart{position:relative;min-height:220px}
.chart svg{display:block;width:100%;overflow:visible}
.chart text{fill:var(--soft);font:12px var(--font);font-variant-numeric:tabular-nums}
.tip{position:absolute;top:0;left:0;z-index:2;pointer-events:none;min-width:190px;padding:10px 12px;border-radius:10px;background:var(--bg);border:1px solid var(--line);box-shadow:0 6px 24px rgba(0,0,0,.35);font-size:13px;line-height:1.45}
.tip b{display:block;margin-bottom:4px;font:600 15px var(--tight)}
.tip div{display:flex;justify-content:space-between;gap:16px;color:var(--soft)}
.tip div span:last-child{color:var(--text);font-variant-numeric:tabular-nums}
.tip .tt{margin-bottom:4px;padding-bottom:4px;border-bottom:1px solid var(--line)}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:10px;color:var(--soft);font-size:12.5px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px}
.k-in{background:var(--up)}.k-out{background:var(--down)}.legend .k-avg{height:2px;width:16px;border-radius:1px;background:var(--text)}
.figs{margin-top:12px}
.figs summary{cursor:pointer;color:var(--soft);font-size:13px}
.figs .tbl{max-width:560px;margin-top:12px}
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.tile{padding:22px 0 20px 22px;border-left:1px solid var(--line);min-width:0}
.tile:first-child{border-left:0;padding-left:0}
.t-l{color:var(--soft);font-size:13.5px}
.t-v{margin-top:4px;font:600 30px var(--tight);letter-spacing:-.02em}
.t-s{margin-top:2px;color:var(--soft);font-size:13px;min-height:20px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t-s .up,.t-s .dn{font-size:9px;vertical-align:1px}
.spark{display:block;margin-top:12px;color:var(--soft2);max-width:100%}
.lh{font:600 17px var(--tight);margin-bottom:6px}
a.row{text-decoration:none;color:inherit}
.top{align-items:start}
.top .row{grid-template-columns:18px 32px minmax(0,1fr) minmax(40px,110px) 128px}
.top.one .row{grid-template-columns:18px 32px minmax(0,1fr) minmax(40px,320px) 128px}
@media (max-width:1240px){.cell{padding:20px 18px}.p1 b{font-size:22px}.p1,.p2,.sc{font-size:13px}}
@media (max-width:1060px){.cell{padding:18px 14px}.p1 b{font-size:19px;margin-right:3px}.p1,.p2,.sc{font-size:12.5px}}
@media (max-width:960px){.strip{grid-template-columns:1fr 1fr}.cell:nth-child(3){border-left:0}.cell:nth-child(n+3){border-top:1px solid var(--line)}}
@media (max-width:900px){
  .hc{max-width:100%;padding:36px 22px 30px}
  .art{opacity:.4;-webkit-mask-image:linear-gradient(180deg,#000 0,transparent 78%);mask-image:linear-gradient(180deg,#000 0,transparent 78%)}
  .meter{grid-template-columns:1fr;gap:14px;padding:20px 22px}
  .m-v b{font-size:52px}
  .cell{padding:18px 22px}
  .tiles{grid-template-columns:1fr 1fr}
  .tile{padding:18px 0 16px 16px}
  .tile:nth-child(odd){border-left:0;padding-left:0}
  .tile:nth-child(n+3){border-top:1px solid var(--line)}
  .top .row,.top.one .row{grid-template-columns:18px 32px minmax(0,1fr) 124px}
}
@media (max-width:560px){.strip{grid-template-columns:1fr}.cell{border-left:0!important;border-top:1px solid var(--line)}.cell:first-child{border-top:0}}
"""

JS = r"""
(function(){
 const root=document.querySelector('[data-view="home"]');if(!root)return;
 const D=JSON.parse(document.getElementById('home-data').textContent).rows;
 const el=root.querySelector('#home-chart'),segEl=root.querySelector('#home-range');
 const S={range:'90'};
 const avg=D.map((r,i)=>{if(i<6)return null;let s=0;for(let k=i-6;k<=i;k++)s+=D[k][1];return s/7});
 const nice=x=>{const p=Math.pow(10,Math.floor(Math.log10(x))),f=x/p;return (f<=1?1:f<=2?2:f<=2.5?2.5:f<=5?5:10)*p};
 const ax=v=>Math.abs(v)<1e-6?'0':(v<0?'−':'')+SOB.fm(v).replace(/\.0(?=[KMB])/,'');
 const wd=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
 function draw(){
  const n=S.range==='all'?D.length:Math.min(D.length,+S.range),o=D.length-n,data=D.slice(o);
  const W=el.clientWidth||640,H=W<560?230:300,m={t:12,r:6,b:24,l:50},iw=W-m.l-m.r,ih=H-m.t-m.b;
  let hi=0,lo=0;data.forEach((r,i)=>{hi=Math.max(hi,r[1],avg[o+i]||0);lo=Math.min(lo,r[1],avg[o+i]||0)});
  const step=nice((hi-lo)/4||1);hi=Math.ceil(hi/step)*step;lo=Math.floor(lo/step)*step;if(hi===lo)hi=lo+step;
  const y=v=>m.t+(hi-v)/(hi-lo)*ih,bw=iw/n,gap=bw>7?2:bw>3.5?1:.4,w=Math.max(.8,bw-gap),rad=Math.min(3,w/2);
  let g='';
  for(let v=lo;v<=hi+1e-6;v+=step){const yy=y(v);g+=`<line x1="${m.l}" x2="${W-m.r}" y1="${yy}" y2="${yy}" stroke="var(--line${Math.abs(v)<1e-6?'-2':''})" stroke-width="1"/><text x="${m.l-8}" y="${yy+4}" text-anchor="end">${ax(v)}</text>`}
  let bars='',lab='';
  data.forEach((r,i)=>{const x=m.l+i*bw+gap/2,v=r[1],y0=y(0),y1=y(v),h=Math.max(Math.abs(y1-y0),v?1:0),rr=Math.min(rad,h);
   if(v>=0)bars+=`<path d="M${x},${y0}V${y0-h+rr}q0,${-rr} ${rr},${-rr}H${x+w-rr}q${rr},0 ${rr},${rr}V${y0}Z" fill="var(--up)"/>`;
   else bars+=`<path d="M${x},${y0}V${y0+h-rr}q0,${rr} ${rr},${rr}H${x+w-rr}q${rr},0 ${rr},${-rr}V${y0}Z" fill="var(--down)"/>`;
   const dd=r[0],day=+dd.slice(8,10);
   if(n<=45?(new Date(dd+'T00:00:00Z').getUTCDay()===1):day===1)lab+=`<text x="${x+w/2}" y="${H-6}" text-anchor="middle">${n<=45?SOB.day(dd):SOB.day(dd).slice(0,3)}</text>`});
  let p='';data.forEach((r,i)=>{const a=avg[o+i];if(a==null)return;p+=(p?'L':'M')+(m.l+i*bw+bw/2).toFixed(1)+','+y(a).toFixed(1)});
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Net capital flow per day"><g>${g}</g>${bars}
   <path d="${p}" fill="none" stroke="var(--surface)" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/><path d="${p}" fill="none" stroke="var(--ink)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
   ${lab}<line id="hx" y1="${m.t}" y2="${m.t+ih}" stroke="var(--ink-3)" stroke-width="1" visibility="hidden"/>
   <rect id="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"/></svg><div class="tip" hidden></div>`;
  const tip=el.querySelector('.tip'),hx=el.querySelector('#hx'),hit=el.querySelector('#hit');
  const move=e=>{const b=el.getBoundingClientRect(),px=e.clientX-b.left,i=Math.max(0,Math.min(n-1,Math.floor((px-m.l)/bw))),r=data[i],cx=m.l+i*bw+bw/2;
   hx.setAttribute('x1',cx);hx.setAttribute('x2',cx);hx.setAttribute('visibility','visible');
   const dt=new Date(r[0]+'T00:00:00Z');
   tip.innerHTML=`<b>${wd[dt.getUTCDay()]}, ${SOB.day(r[0])}</b><div class="tt"><span>Net flow</span><span>${SOB.signed(r[1])}</span></div><div><span>Stablecoins</span><span>${SOB.signed(r[2])}</span></div><div><span>ETH</span><span>${SOB.signed(r[3])}</span></div>`;
   tip.hidden=false;const tw=tip.offsetWidth;tip.style.transform=`translate(${Math.max(0,Math.min(W-tw,cx+(cx+tw+16>W?-tw-12:12)))}px,${m.t+4}px)`};
  hit.addEventListener('pointermove',move);hit.addEventListener('pointerdown',move);
  hit.addEventListener('pointerleave',()=>{tip.hidden=true;hx.setAttribute('visibility','hidden')});
 }
 function render(){SOB.seg(segEl,[['30','30D'],['90','90D'],['all','All']],S.range,v=>{S.range=v;render()});draw()}
 render();
 let t;window.addEventListener('resize',()=>{clearTimeout(t);t=setTimeout(draw,120)});
 window.addEventListener('sob:view',e=>{if(e.detail==='home')draw()});
 window.addEventListener('sob:theme',draw);

 /* The site's ASCII: two chain links drawn from a density ramp over a slow current, and the score as a level in the same hand. */
 const card=root.querySelector('.herocard'),art=root.querySelector('.art'),lower=root.querySelector('.lower'),mart=root.querySelector('.m-art');
 if(!card||!art)return;
 const cur=art.querySelector('.cur'),shp=art.querySelector('.shp');
 const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
 const SPEED=parseFloat(art.dataset.speed)||1,RAMP='.:-=+*#%';
 let cols=0,rows=0,cw=6.6,lh=13,mask=null,tt=30+Math.random()*40,last=0,on=true;
 const measure=()=>{const c=document.createElement('canvas').getContext('2d');c.font=getComputedStyle(cur).font;cw=c.measureText('MMMMMMMMMM').width/10||6.6};
 function field(x,y,k){const X=x*0.055,Y=y*0.10;
  return Math.sin(X*1.25+Math.sin(Y*0.85+k*0.33)*1.7+k*0.22)+0.85*Math.sin(Y*1.6-Math.cos(X*0.65-k*0.19)*1.3-k*0.27)}
 function buildMask(){
  mask=null;if(rows<10||cols<30)return;
  const sx=6,sy=12,cv=document.createElement('canvas');cv.width=cols*sx;cv.height=rows*sy;
  const g=cv.getContext('2d');g.fillStyle='#000';g.fillRect(0,0,cv.width,cv.height);
  const H=Math.min(cv.height*0.40,cv.width*0.145),Wd=H*1.75,th=H*0.30,cx=cv.width*0.74,cy=cv.height*0.50,off=Wd*0.36;
  const link=(dx,stroke,width)=>{g.save();g.translate(cx,cy);g.rotate(-24*Math.PI/180);g.translate(dx,0);
   const w=Wd,h=H,r=h/2;g.beginPath();g.moveTo(-w/2+r,-h/2);g.lineTo(w/2-r,-h/2);g.arc(w/2-r,0,r,-Math.PI/2,Math.PI/2);g.lineTo(-w/2+r,h/2);g.arc(-w/2+r,0,r,Math.PI/2,Math.PI*1.5);g.closePath();
   g.lineWidth=width;g.strokeStyle=stroke;g.stroke();g.restore()};
  link(-off,'#fff',th);
  link(off,'#000',th*1.9);link(off,'#fff',th);
  g.save();g.beginPath();g.rect(cx-Wd*0.05,cy,Wd*0.40,H*1.2);g.clip();
  link(-off,'#000',th*1.9);link(-off,'#fff',th);g.restore();
  const px=g.getImageData(0,0,cv.width,cv.height).data;mask=new Float32Array(cols*rows);
  for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){let sum=0;for(let y=0;y<sy;y+=3)for(let x=0;x<sx;x+=2)sum+=px[((r*sy+y)*cv.width+c*sx+x)*4];mask[r*cols+c]=sum/((sy/3)*(sx/2)*255)}
 }
 function drawArt(){
  let a='',b='';
  for(let r=0;r<rows;r++){let la='',lb='';const y=r*1.9;
   for(let c=0;c<cols;c++){
    const mk=mask?mask[r*cols+c]:0;
    if(mk>0.42){const f=(field(c*1.7,y*1.7,tt*1.3)+1.9)/3.8;lb+=RAMP.charAt(Math.max(0,Math.min(RAMP.length-1,Math.floor((0.15+0.85*f)*RAMP.length))));la+=' ';continue}
    if(mk>0.10){lb+=(c+r)%2?':':'.';la+=' ';continue}
    lb+=' ';
    const v=field(c,y,tt)*2.3,d=Math.abs(v-Math.round(v));
    if(d<0.07){const gx=field(c+1,y,tt)-field(c-1,y,tt),gy=field(c,y+1.9,tt)-field(c,y-1.9,tt);
     const ang=Math.atan2(gx,-gy),a8=((ang+Math.PI)/(Math.PI/4))|0;la+='-/|\\-/|\\'.charAt(a8%8)}
    else la+=' ';
   }
   a+=la+'\n';b+=lb+'\n'}
  cur.textContent=a;shp.textContent=b;
 }
 /* the meter: five rows of level, a tick row and a label row; the level's edge moves like a waterline.
    Where the score read a week ago is a second level in the same hand, fainter: past this week's edge when the
    score has fallen, and as a waterline seen through this week's level when it has risen. */
 const mc=mart&&mart.querySelector('.cur'),ms=mart&&mart.querySelector('.shp'),ml=mart&&mart.querySelector('.lab'),mw=mart&&mart.querySelector('.was'),score=mart?+mart.dataset.score:0;
 const was=mart&&mart.dataset.was!==undefined&&mw?+mart.dataset.was:null;
 let mcols=0;
 const put=(arr,pos,text)=>{for(let i=0;i<text.length;i++){const k=pos+i;if(k>=0&&k<arr.length)arr[k]=text[i]}};
 function drawMeter(){
  if(!mart||mcols<20)return;
  const R=5,edge=score/100*(mcols-1),c40=Math.round(0.4*(mcols-1)),c60=Math.round(0.6*(mcols-1));
  const wedge=was==null?null:was/100*(mcols-1),under=wedge!=null&&wedge<edge-2.5;
  let a='',b='',w='';
  for(let r=0;r<R;r++){let la='',lb='',lw='';const e=edge+Math.sin(r*1.35+tt*1.6)*1.1+Math.sin(r*0.7-tt*0.9)*0.6;
   const e2=wedge==null?-1:wedge+Math.sin(r*1.35+tt*1.6+2.1)*1.1+Math.sin(r*0.7-tt*0.9+1.3)*0.6,seam=under?Math.round(e2):-1;
   for(let c=0;c<mcols;c++){
    if(c<=e){
     if(c===seam||c===seam+1){lb+=' ';la+=' ';lw+=c===seam?':':' '}
     else{const f=(field(c*1.7,r*3.4,tt*1.3)+1.9)/3.8;lb+=RAMP.charAt(Math.max(0,Math.min(RAMP.length-1,Math.floor((0.3+0.7*f)*RAMP.length))));la+=' ';lw+=' '}}
    else if(c<=e2){const f=(field(c*1.7+40,r*3.4,tt*1.3)+1.9)/3.8;lw+=RAMP.charAt(Math.max(0,Math.min(RAMP.length-1,Math.floor((0.1+0.6*f)*RAMP.length))));lb+=' ';la+=' '}
    else{lb+=' ';lw+=' ';la+=(c===c40||c===c60)?'|':((c+r)%2?':':'.')}
   }
   a+=la+'\n';b+=lb+'\n';w+=lw+'\n'}
  const ticks=new Array(mcols).fill(' '),labs=new Array(mcols).fill(' ');
  put(ticks,0,'0');put(ticks,c40-1,'40');put(ticks,c60-1,'60');put(ticks,mcols-3,'100');
  const mid=(x,y,s)=>put(labs,Math.round((x+y)/2-s.length/2),s);
  mid(0,c40,'OUTFLOW');mid(c40,c60,'BALANCED');mid(c60,mcols-1,'INFLOW');
  let top='';
  if(wedge!=null){const t=was+' A WEEK AGO',row=new Array(mcols).fill(' '),cp=Math.round(wedge);
   put(row,Math.max(0,cp+t.length>mcols?cp-t.length+1:cp),t);top=row.join('')+'\n';a='\n'+a;b='\n'+b;w='\n'+w}
  mc.textContent=a;ms.textContent=b;if(mw)mw.textContent=w;ml.textContent=top+'\n'.repeat(R)+ticks.join('')+'\n'+labs.join('');
 }
 function resize(){card.style.setProperty('--lowerh',lower.offsetHeight+'px');
  measure();cols=Math.ceil(art.clientWidth/cw);rows=Math.ceil(art.clientHeight/lh);mcols=mart?Math.floor(mart.clientWidth/cw):0;buildMask();drawArt();drawMeter()}
 function loop(ts){requestAnimationFrame(loop);if(!on||root.hidden)return;if(ts-last<1000/24)return;last=ts;tt+=0.035*SPEED;drawArt();drawMeter()}
 if('ResizeObserver' in window)new ResizeObserver(()=>resize()).observe(card);
 if('IntersectionObserver' in window)new IntersectionObserver(es=>{on=es[0].isIntersecting}).observe(card);
 window.addEventListener('sob:view',e=>{if(e.detail==='home')resize()});
 resize();if(!reduce)requestAnimationFrame(loop);
})();
"""


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    m = model(sys.argv[1])
    v = view(m)
    out = SH.page(v, f"Data through {dshort(m['asof'])}", m["meta"])
    tmp = sys.argv[2] + ".tmp"
    open(tmp, "w", encoding="utf-8").write(out); os.replace(tmp, sys.argv[2])
    ago = "not enough history yet" if m.get("score_ago") is None else f"{m['score_ago']:.0f}"
    print(f"home: {headline_text(m)} score {m['score']:.0f}, a week ago: {ago}" if m["score"] is not None else "home: built without a score")


if __name__ == "__main__":
    main()
