"""Render the Holder strength page from holder scans (holders_scan.py output).
Usage: build_holders_page.py <states_dir> <out.html> [universe.json] [flows_cache]   (reads <SYM>.state.json.gz files)"""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows_model as FM
import holders_model as HM
import build_flows_page as P
import rh

NAMES = {"STANDARD": "The Standard Reserve"}      # where the token's own name isn't the project's


def fill_kinds(st):
    """Look up wallet kinds for big past holders the scan didn't classify (it only checked today's $1+ holders)."""
    want = set()
    for back in (8, 15, 31, 38):
        bals = HM.balances_at(st, HM.shift(st["asof"], -back))
        want |= {a for a, _ in sorted(bals.items(), key=lambda kv: -kv[1])[:300]}
    todo = [a for a in want if a not in st["kinds"]]
    if todo:
        res = rh.batch("eth_getCode", [[a, "latest"] for a in todo], size=50)
        for a, c in zip(todo, res):
            c = c or "0x"
            st["kinds"][a] = "eoa" if c == "0x" else "7702" if c.startswith("0xef0100") else f"c{len(c) // 2}"
    return len(todo)


def build(sdir, names=None, passthrough=None):
    toks, unbuildable, unpriced = [], [], []
    for p in sorted(glob.glob(os.path.join(sdir, "*.state.json.gz"))):
        st = HM.load(p)
        if fill_kinds(st):
            import gzip
            with gzip.open(p, "wt") as f:
                json.dump(st, f, separators=(",", ":"))
        if not st.get("rebuildable", True):
            unbuildable.append(st["symbol"]); continue
        t = HM.token(st)
        if not t:
            unpriced.append(st["symbol"]); continue
        if t:
            t["name"] = NAMES.get(t["symbol"]) or (names or {}).get(t["symbol"]) or t["symbol"]
            t["moved"] = HM.what_moved(t)
            toks.append(t)
    asof = max(t["asof"] for t in toks)
    stale = sorted(t["symbol"] for t in toks if t["asof"] != asof)       # state couldn't roll (a Flows day is missing)
    toks = [t for t in toks if t["asof"] == asof]
    for t in toks:
        t["flags"] = ["pass-through"] if (passthrough or {}).get(t["symbol"], 0) >= 60 else []
    return {"asof": asof, "tokens": toks, "stale": stale, "unbuildable": sorted(unbuildable), "unpriced": sorted(unpriced), "cats": [{"id": c, "label": l} for c, l in FM.CATS],
            "labels": HM.LABELS, "weights": HM.WEIGHTS}


def read(m):
    T = sorted(m["tokens"], key=lambda t: -t["now"]["score"])
    if not T:
        return "No token could be scored today."
    clean = [t for t in T if not t.get("flags")]
    top, low = (clean or T)[0], T[-1]
    up = sum(1 for t in T if t["now"]["score"] > t["week_ago"]["score"] + 0.5)
    dn = sum(1 for t in T if t["now"]["score"] < t["week_ago"]["score"] - 0.5)
    mover = max(clean or T, key=lambda t: abs(t["now"]["score"] - t["week_ago"]["score"]))
    d = mover["now"]["score"] - mover["week_ago"]["score"]
    s = (f"{top['symbol']} has the strongest holders of the {len(T)} RH tokens we score ({top['now']['score']:.0f} out of 100), "
         f"{low['symbol']} the weakest ({low['now']['score']:.0f}). ")
    if abs(d) < 0.5:
        s += "Scores barely moved this week."
    else:
        s += (f"Scores {'fell' if dn >= up else 'rose'} for {max(dn, up)} of {len(T)} this week; {mover['symbol']} moved most "
              f"({'+' if d > 0 else '−'}{abs(d):.0f}) {HM.why(mover)}.")
    flagged = [t for t in T if t.get("flags") and t["now"]["score"] > top["now"]["score"]]
    if flagged:
        f = flagged[0]
        s += f" {f['symbol']} scores {f['now']['score']:.0f} but trades mostly as pass-through volume, so it's flagged."
    return s


JS = r"""
const M=JSON.parse(document.getElementById('h-data').textContent);
const S={c:'all',sort:'score',sel:null};
const K=['conviction','top','breadth','profit'];
const pct=x=>x==null?'—':(x>=0?'+':'−')+Math.abs(x).toFixed(1)+'%';
function seg(el,key,opts){el.innerHTML=opts.map(([v,l])=>`<button data-v="${v}" class="${S[key]===v?'on':''}">${l}</button>`).join('');
 el.onclick=e=>{const b=e.target.closest('button');if(!b)return;S[key]=b.dataset.v;if(key==='c')S.sel=null;render()}}
function meter(v){return `<div class="meter"><span style="width:${Math.max(1,v).toFixed(1)}%"></span></div>`}
function render(){
 seg(document.getElementById('s-c'),'c',[['all','All'],...M.cats.filter(c=>M.tokens.some(t=>t.category===c.id)).map(c=>[c.id,c.label])]);
 seg(document.getElementById('s-s'),'sort',[['score','Strongest first'],['chg','Biggest change']]);
 const T=M.tokens.filter(t=>S.c==='all'||t.category===S.c).sort((a,b)=>S.sort==='score'?b.now.score-a.now.score:Math.abs(b.now.score-b.week_ago.score)-Math.abs(a.now.score-a.week_ago.score));
 if(!S.sel||!T.includes(S.sel))S.sel=T.find(t=>!t.flags.length)||T[0];
 document.getElementById('count').textContent=T.length+' tokens';
 document.getElementById('hl').innerHTML=T.map((t,i)=>{const d=t.now.score-t.week_ago.score;
  return `<div class="hrow ${t===S.sel?'sel':''}" data-s="${t.symbol}"><span class="n">${i+1}</span><span class="tk"><b>${t.symbol}</b>${t.flags.includes('pass-through')?'<i class="pt" title="Most of its volume is pass-through: see Flows">pass-through</i>':''}<small>${t.name}</small></span>
  <span class="sc">${Math.round(t.now.score)}</span>${meter(t.now.score)}<span class="val ${Math.abs(d)<0.5?'':d>0?'p':'m'}">${Math.abs(d)<0.5?'±0':(d>0?'+':'−')+Math.abs(d).toFixed(0)}</span></div>`}).join('');
 document.getElementById('hl').onclick=e=>{const r=e.target.closest('.hrow');if(!r)return;S.sel=M.tokens.find(t=>t.symbol===r.dataset.s);render()};
 const t=S.sel;if(!t)return;const f=t.now.facts,g=t.week_ago.facts;
 const rows=[['conviction',`${f.conviction.toFixed(0)}% of holder supply untouched for 30+ days`,`${g.conviction.toFixed(0)}% a week ago`],
  ['top',`50 largest holders ${f.top_chg>=0?'added':'cut'} ${Math.abs(f.top_chg).toFixed(1)}% this week`,`${g.top_chg>=0?'added':'cut'} ${Math.abs(g.top_chg).toFixed(1)}% the week before`],
  ['breadth',`${f.holders.toLocaleString()} holders worth $10+ (${pct(f.holders_chg)} on the week)`+(f.kept!=null?`; ${f.kept.toFixed(0)}% of ${f.new} new holders still in`:''),`${g.holders.toLocaleString()} a week ago`],
  ['profit',`${f.in_profit.toFixed(0)}% of holder supply in profit`,`${g.in_profit.toFixed(0)}% a week ago`]];
 document.getElementById('det').innerHTML=`<div class="hd"><b>${t.symbol}</b><span>${t.name}</span><a class="chip" style="margin-left:auto" href="https://www.geckoterminal.com/robinhood/tokens/${t.token}" target="_blank" rel="noopener">Chart ↗</a></div>
  <div class="big"><b>${Math.round(t.now.score)}</b><span>out of 100 · ${Math.round(t.week_ago.score)} a week ago · price ${pct(t.price_chg_7d)} on the week</span></div>
  <p class="moved">${t.moved}</p>`+
  rows.map(([k,a,b])=>`<div class="part"><div class="ph"><span>${M.labels[k]} <i>${Math.round(M.weights[k]*100)}%</i></span><b>${Math.round(t.now.parts[k])}</b></div>${meter(t.now.parts[k])}<p>${a} <span>· ${b}</span></p></div>`).join('')+
  (t.flags.includes('pass-through')?`<p class="flag">Most of this token's trading volume comes from wallets that held none before and none after (Flows' pass-through share). Its price, and so its in-profit share, may be propped up by wash trading.</p>`:'')+
  `<p class="note">Top 10 holders own ${f.top10.toFixed(0)}% of holder supply. ${t.transfers.toLocaleString()} transfers read, every one since the token's first pool.</p>`;
}
render();
"""

EXTRA_CSS = r"""
.fl .hrow{display:grid;grid-template-columns:22px minmax(0,150px) 44px minmax(0,1fr) 46px;gap:12px;align-items:center;padding:8px;border-radius:9px;cursor:pointer}
.fl .hrow:hover{background:var(--card2)}
.fl .hrow.sel{background:var(--card2);box-shadow:inset 0 0 0 1px var(--line2)}
.fl .hrow .n{font:12px var(--mono);color:var(--faint);text-align:right}
.fl .sc{font-size:18px;font-weight:650;text-align:right;letter-spacing:-.02em}
.fl .meter{height:8px;background:var(--card2);border-radius:4px;overflow:hidden}
.fl .meter span{display:block;height:100%;background:var(--lime-ink);border-radius:4px}
.fl #det .hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.fl #det .hd b{font-size:19px}
.fl #det .hd span{color:var(--soft);font-size:13px}
.fl .big{display:flex;align-items:baseline;gap:12px;margin:14px 0 4px;flex-wrap:wrap}
.fl .big b{font-size:52px;letter-spacing:-.04em;line-height:1}
.fl .big span{color:var(--soft);font-size:13px}
.fl .moved{font-size:14.5px;line-height:1.55;margin:10px 0 16px}
.fl .part{border-top:1px solid var(--line);padding:12px 0}
.fl .ph{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;font-size:14px;font-weight:600}
.fl .ph i{font:500 11px var(--mono);color:var(--faint);font-style:normal;margin-left:6px}
.fl .part p{margin:8px 0 0;font-size:13px;color:var(--ink);line-height:1.5}
.fl .part p span{color:var(--faint)}
@media (max-width:620px){.fl .hrow{grid-template-columns:18px minmax(0,90px) 34px minmax(0,1fr) 36px;gap:8px;padding:8px 4px}}
"""


def page(m):
    data = json.dumps(m, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Holders content</title>
<!--flows-head--><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet"><!--/flows-head-->
<style id="flows-css">{P.CSS}{EXTRA_CSS}</style></head><body>
<!--flows--><div class="fl">
<div class="over">Holder strength<span class="beta">BETA</span></div>
<h1>Are RH holders holding?</h1>
<p class="read">{read(m)}</p>
<p class="meta">Through {FM.dlong(m['asof'])} UTC, from every transfer since each token's first pool. Not investment advice.</p>
<div class="ctl"><div class="seg" id="s-c"></div><div class="seg" id="s-s"></div><span class="count" id="count"></span></div>
<div class="grid">
 <section class="card"><div class="ch"><div><h2>Holder strength, 0 to 100</h2><p class="sub">Change is against a week ago. Click a token for its four parts.</p></div></div>
  <div class="rank" id="hl"></div></section>
 <section class="card" id="det"></section>
</div>
<details class="how"><summary>How to read it</summary><dl>
<dt>Holders</dt><dd>Wallets holding the token, including smart wallets (EIP-7702 and small wallet proxies). Pools, the Uniswap v4 pool manager, other contracts and burn addresses are left out.</dd>
<dt>Conviction, 35%</dt><dd>The share of holder supply held for 30+ days by wallets that moved none of it out in that time. 0% scores 0, 80% or more scores 100.</dd>
<dt>Top holders, 25%</dt><dd>How the 50 largest holders of a week ago changed their combined balance since. Cutting 10% or more scores 0, flat scores 50, adding 10% or more scores 100.</dd>
<dt>Breadth, 20%</dt><dd>The number of holders worth $10+ against a week ago (at today's price, so price moves don't change the count), averaged with the share of wallets that became holders 8 to 14 days ago and still hold. Shrinking 10% scores 0, growing 10% scores 100; keeping 80% of new holders scores 100.</dd>
<dt>In profit, 20%</dt><dd>The share of holder supply whose last top-up was at or below today's price: holders sitting on gains have less reason to sell into a bounce. The score is that share.</dd>
<dt>Fixed scales</dt><dd>Every part uses the same scale for every token and every day, so a 60 means the same thing on PONS as on a meme, and today's score can be compared with last month's.</dd>
<dt>Tokens</dt><dd>Memes, AI and agent tokens, and ecosystem tokens from Flows. Stock tokens are left out: their holders follow the stock, not the project.{(' Not shown today: ' + ', '.join(m['stale']) + " (a day of transfers is still missing).") if m.get('stale') else ''}{(' Not scored: ' + ', '.join(m['unpriced']) + " (not enough daily prices)." ) if m.get('unpriced') else ''}{(' Not scored: ' + ', '.join(m['unbuildable']) + ", whose balances change without transfer events (reflections), so they can't be rebuilt from transfers.") if m.get('unbuildable') else ''}</dd>
</dl></details>
<p class="fsrc">Data: Robinhood Chain (every transfer since each token's first pool) and GeckoTerminal (prices), through {FM.dlong(m['asof'])} UTC.</p>
</div>
<script type="application/json" id="h-data">{data}</script>
<script>{JS}</script><!--/flows-->
</body></html>"""


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    names = {}
    if len(sys.argv) > 3:
        names = {t["symbol"]: t.get("name") or t["symbol"] for t in json.load(open(sys.argv[3]))}
    pt = {}
    if len(sys.argv) > 4:                    # flows cache: flag tokens whose 7D volume is mostly pass-through
        fm = FM.build(sys.argv[4])
        w = "7d" if "7d" in fm["windows"] else "1d"
        pt = {t["symbol"]: (t["w"][w].get("passthrough") or 0) for t in fm["tokens"]}
    m = build(sys.argv[1], names, pt)
    # keep a daily score history next to the states, for trend lines later
    hp = os.path.join(sys.argv[1], "history.json")
    hist = json.load(open(hp)) if os.path.exists(hp) else {}
    hist[m["asof"]] = {t["symbol"]: t["now"]["score"] for t in m["tokens"]}
    json.dump(hist, open(hp, "w"), separators=(",", ":"))
    html = page(m)
    tmp = sys.argv[2] + ".tmp"
    open(tmp, "w").write(html)
    os.replace(tmp, sys.argv[2])
    print(read(m))
    for t in sorted(m["tokens"], key=lambda t: -t["now"]["score"]):
        print(f"  {t['symbol']:9s} {t['now']['score']:5.1f} (wk {t['week_ago']['score']:5.1f}) parts {t['now']['parts']} | {t['moved']}")


if __name__ == "__main__":
    main()
