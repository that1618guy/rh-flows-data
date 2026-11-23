"""The Holders page: how firmly each Robinhood Chain token is held, and where its holders stand.

  holder strength   0 to 100 from four parts on fixed scales (holders_model.py), with what moved it over the
                    window picked at the top: 1D, 7D, or 30D once most tokens have 60 days of prices
  where they stand  holder supply split by how far each holder is from their entry, and the average holder's result

Crypto tokens with $50K+ of pool liquidity and 30+ days of daily prices. Built from the holder states the daily job
rolls forward; the only chain read is one batched lookup for big holders the state hasn't classified yet.

Usage: build_holders_page.py <cache_dir> <out.html>     cache_dir holds holders/<SYM>.state.json.gz and
       tokens_meta.json. Also appends the day's scores to holders/history.json.
"""
import glob, gzip, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import holders_model as HM
import inflow as IN
import site_shell as SH
import rh
from site_shell import fm, pct, dshort

STEADY = 0.6            # share of tokens that must move the same way for the headline to say so
OVER = {"1d": "on the day", "7d": "this week", "30d": "over 30 days"}
STAND = ["Down 50% or more", "Down less than 50%", "Up less than 2×", "Up 2× or more"]


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


def model(cache_dir):
    sdir = os.path.join(cache_dir, "holders")
    meta = json.load(open(os.path.join(cache_dir, "tokens_meta.json")))
    toks, left = [], {"thin": [], "unpriced": [], "unbuildable": [], "passed": []}
    passed = IN.passed_through_in(cache_dir)
    for p in sorted(glob.glob(os.path.join(sdir, "*.state.json.gz"))):
        st = HM.load(p)
        sym = st["symbol"]
        if sym in passed:
            left["passed"].append(sym); continue
        if (meta.get(sym) or {}).get("liq", 0) < IN.MIN_LIQ:
            left["thin"].append(sym); continue
        if not st.get("rebuildable", True):
            left["unbuildable"].append(sym); continue
        if fill_kinds(st):
            tmp = p + ".tmp"
            with gzip.open(tmp, "wt") as f:
                json.dump(st, f, separators=(",", ":"))
            os.replace(tmp, p)
        t = HM.token(st)
        if not t:
            left["unpriced"].append(sym); continue
        t["moved"] = {w: HM.what_moved(t, w) for w, _ in HM.WINDOWS}
        toks.append(t)
    if not toks:
        raise RuntimeError("holders: no token could be scored")
    asof = max(t["asof"] for t in toks)
    left["stale"] = sorted(t["symbol"] for t in toks if t["asof"] != asof)
    toks = [t for t in toks if t["asof"] == asof]
    # a window is offered when most tokens can be compared over it (30 days needs 60 days of prices)
    wins = [w for w, _ in HM.WINDOWS if sum(1 for t in toks if t["then"][w]) >= 0.8 * len(toks)]
    return {"asof": asof, "tokens": toks, "left": left, "meta": meta, "wins": wins}


def read(m, wid="7d"):
    T = sorted(m["tokens"], key=lambda t: -t["now"]["score"])
    N = len(T)
    C = [t for t in T if t["then"][wid]]                   # the tokens that can be compared over this window
    M = len(C)
    d = lambda t: t["now"]["score"] - t["then"][wid]["score"]
    up = sum(1 for t in C if d(t) > 0.5); dn = sum(1 for t in C if d(t) < -0.5)
    h = ("Holders are getting weaker." if dn >= STEADY * M else "Holders are getting stronger." if up >= STEADY * M
         else "Holders are holding steady.")
    top, low = T[0], T[-1]
    p = (f"<b>{top['symbol']}</b> has the firmest holders of the {N} tokens we score ({top['now']['score']:.0f} out of 100), "
         f"{low['symbol']} the weakest ({low['now']['score']:.0f}).")
    mover = max(C, key=lambda t: abs(d(t)))
    if abs(d(mover)) >= 0.5:
        p += (f" Scores {'fell' if dn >= up else 'rose'} for {max(dn, up)} of {M} {OVER[wid]}; {mover['symbol']} moved most "
              f"({'+' if d(mover) > 0 else SH.MINUS}{abs(d(mover)):.0f}) {HM.why(mover, wid)}.")
    under = sum(1 for t in T if (t["now"]["facts"].get("avg_pl") or 0) < 0)
    p += f" The average holder is underwater in {under} of {N}."
    return {"h": h, "p": p}


def view(m, href=lambda i: f"/{i}"):
    T, W = [], m["wins"]
    for t in m["tokens"]:
        n = t["now"]
        T.append({"s": t["symbol"], "score": n["score"], "parts": n["parts"], "f": n["facts"],
                  "d": {w: round(n["score"] - t["then"][w]["score"], 1) if t["then"][w] else None for w in W},
                  "moved": {w: t["moved"][w] for w in W}, "chg": {w: t["price_chg"][w] for w in W}})
    data = {"asof": m["asof"], "tokens": T, "wins": W, "read": {w: read(m, w) for w in W}, "labels": HM.LABELS,
            "weights": {k: round(v * 100) for k, v in HM.WEIGHTS.items()}, "stand": STAND,
            "names": {s: x["name"] for s, x in m["meta"].items()}, "addr": {s: x["address"] for s, x in m["meta"].items()},
            "gt": "https://www.geckoterminal.com/robinhood/tokens/", "links": {"flows": href("flows"), "smart": href("smart-money")}}
    lo = m["left"]
    out = [x for x in (f"thin liquidity: {', '.join(lo['thin'])}" if lo["thin"] else "",
                       f"{IN.PASS_NOTE}: {', '.join(lo['passed'])}" if lo.get("passed") else "",
                       f"too few daily prices: {', '.join(lo['unpriced'])}" if lo["unpriced"] else "",
                       f"can't be rebuilt from transfers: {', '.join(lo['unbuildable'])}" if lo["unbuildable"] else "",
                       f"waiting on a day of data: {', '.join(lo['stale'])}" if lo["stale"] else "") if x]
    left = f" Left out today ({'; '.join(out)})." if out else ""
    body = f"""
<section class="pg-head">
<p class="eyebrow">Holders · through {dshort(m['asof'])} <span class="nb">· updated daily</span></p>
<h1 id="h-h"></h1>
<p class="lede" id="h-p"></p>
<div class="ctl pg-ctl"><div class="seg" id="h-win" role="group" aria-label="Change over"></div></div>
</section>

<section class="sec">
<div class="md">
<div class="md-list"><div class="sec-h"><div class="seg" id="h-sort" role="group" aria-label="Order"></div><p class="md-sum">Holder strength, 0 to 100</p></div>
<div class="panel"><ol class="rank" id="h-rows"></ol></div></div>
<aside class="panel insp" id="h-insp" aria-live="polite"></aside>
</div>
</section>

<details class="how"><summary>How this is measured</summary><dl>
<dt>1D, 7D, 30D</dt><dd>The window sets what today's score is compared with: a day, a week or 30 days ago. The score itself is the same in all three, and its parts keep their own spans (top holders over 7 days, conviction over 30). A token gets a 30-day comparison once it has 60 days of prices.</dd>
<dt>Who is a holder</dt><dd>Wallets only. Pools, other contracts and burn addresses are left out. Balances are rebuilt from every transfer since the token's first pool and match its total supply.</dd>
<dt>Conviction (35%)</dt><dd>Share of holder supply held 30+ days by wallets that moved none out in that time. 0% scores 0, 80% scores 100.</dd>
<dt>Top holders (25%)</dt><dd>How the 50 largest holders of a week ago changed their combined balance. −10% scores 0, flat scores 50, +10% scores 100.</dd>
<dt>Breadth (20%)</dt><dd>Holders worth $10+ against a week ago, averaged with the share of last week's new holders who are still in.</dd>
<dt>In profit (20%)</dt><dd>Share of holder supply whose last top-up was at or below today's price.</dd>
<dt>Where holders stand</dt><dd>Each holder's entry is the price on the day they last added. Supply is split by how far today's price is from that entry; the average holder's result is today's price against the supply-weighted average entry. Not part of the score.</dd>
<dt>Included</dt><dd>Crypto tokens on Robinhood Chain with $50K+ of pool liquidity and 30+ days of daily prices.{left}</dd>
</dl></details>
"""
    return {"id": "holders", "title": "Holders", "desc": "How firmly each Robinhood Chain token is held, and where its holders stand.",
            "body": body, "css": CSS, "js": JS, "data": data}


CSS = r"""
.pt{margin-top:10px}
.pt-r{padding:11px 0;border-top:1px solid var(--line)}
.pt-r:first-child{border-top:0}
.pt-t{display:flex;justify-content:space-between;align-items:baseline;gap:12px;font-size:14px;font-weight:500}
.pt-t small{color:var(--ink-3);font-weight:400;font-size:12.5px;margin-left:6px}
.pt-t b{font-weight:620;font-variant-numeric:tabular-nums}
.pt-b{height:4px;margin:8px 0 7px;border-radius:2px;background:var(--raised)}
.pt-b i{display:block;height:100%;border-radius:2px;background:var(--ink-2)}
.pt-r p{margin:0;color:var(--ink-3);font-size:12.5px;line-height:1.4}
.st{display:flex;gap:2px;height:10px;margin-top:12px}
.st i{display:block;min-width:2px;border-radius:2px}
.st .s0,.sk .s0{background:var(--down)}
.st .s1,.sk .s1{background:color-mix(in srgb,var(--down) 42%,var(--raised))}
.st .s2,.sk .s2{background:color-mix(in srgb,var(--up) 42%,var(--raised))}
.st .s3,.sk .s3{background:var(--up)}
.sk{display:grid;grid-template-columns:1fr 1fr;gap:6px 16px;margin-top:12px;font-size:12.5px;color:var(--ink-2)}
.sk span{display:flex;align-items:center;gap:7px}
.sk i{display:inline-block;flex:none;width:9px;height:9px;border-radius:3px}
.sk b{margin-left:auto;color:var(--ink);font-weight:550;font-variant-numeric:tabular-nums}
.val .dl{font-size:12px;color:var(--ink-3);font-weight:400}
.val .dl i{font-style:normal;font-size:8px;vertical-align:1px;margin-right:3px}
"""

JS = r"""
(function(){
 const root=document.querySelector('[data-view="holders"]');if(!root)return;
 const D=JSON.parse(document.getElementById('holders-data').textContent),$=id=>root.querySelector('#'+id);
 const {fm,signed,pct,esc,av}=SOB,narrow=window.matchMedia('(max-width:860px)'),K=['conviction','top','breadth','profit'];
 const S={win:D.wins.includes('7d')?'7d':D.wins[0],sort:'top',open:null,all:false},TOP=10;
 const WL={'1d':'1D','7d':'7D','30d':'30D'},OV={'1d':'on the day','7d':'this week','30d':'in 30 days'},HD={'1d':'On the day','7d':'This week','30d':'In 30 days'};
 const dl=d=>d==null?'under 60 days old':Math.abs(d)<0.5?`no change ${OV[S.win]}`:`<i class="${d>0?'up':'dn'}">${d>0?'▲':'▼'}</i>${Math.abs(d).toFixed(0)} ${OV[S.win]}`;
 function fact(k,f){
  if(k==='conviction')return `${f.conviction.toFixed(0)}% of holder supply has sat untouched for 30+ days.`;
  if(k==='top')return Math.abs(f.top_chg)<0.05?'The 50 largest holders were flat this week.':`The 50 largest holders ${f.top_chg>=0?'added':'cut'} ${Math.abs(f.top_chg).toFixed(1)}% this week. The top 10 hold ${f.top10.toFixed(0)}%.`;
  if(k==='breadth')return `${f.holders.toLocaleString()} holders worth $10+, ${pct(f.holders_chg)} on the week${f.kept==null?'':`; ${f.kept.toFixed(0)}% of last week's new holders stayed`}.`;
  return `${f.in_profit.toFixed(0)}% of holder supply was last added at or below today's price.`}
 function row(t,i){
  const on=S.open===t.s,inl=on&&narrow.matches,star=SOB.watch.get().includes(t.s)?'<span class="star">★</span>':'';
  return `<li class="${on?'sel':''}"><button type="button" class="row" data-s="${t.s}" aria-pressed="${on}"><span class="n">${i}</span>${av(t.s)}
   <span><span class="tks">${star}${esc(t.s)}</span><span class="sub">${esc(D.names[t.s]||t.s)}</span></span>
   <span class="bar nt"><i style="width:${Math.max(t.score,2).toFixed(0)}%"></i></span>
   <span class="val">${t.score.toFixed(0)}<small class="dl">${dl(t.d[S.win])}</small></span></button>${inl?`<div class="inl">${detail(t)}</div>`:''}</li>`}
 function detail(t){
  const f=t.f,w=SOB.watch.get().includes(t.s),d=t.d[S.win];
  const parts=K.map(k=>`<div class="pt-r"><div class="pt-t"><span>${D.labels[k]}<small>${D.weights[k]}% of the score</small></span><b>${t.parts[k].toFixed(0)}</b></div><div class="pt-b" role="img" aria-label="${t.parts[k].toFixed(0)} out of 100"><i style="width:${Math.max(t.parts[k],2)}%"></i></div><p>${fact(k,f)}</p></div>`).join('');
  const st=f.stand?`<p class="i-t">Where holders stand against their entry</p><div class="st" role="img" aria-label="Holder supply by distance from entry">${f.stand.map((x,i)=>x>0?`<i class="s${i}" style="flex:${x}" title="${D.stand[i]}: ${x.toFixed(0)}%"></i>`:'').join('')}</div>
   <div class="sk">${f.stand.map((x,i)=>`<span><i class="s${i}"></i>${D.stand[i]}<b>${x.toFixed(0)}%</b></span>`).join('')}</div>
   ${f.avg_pl==null?'':`<p class="i-s">The average holder is ${f.avg_pl>=0?'up':'down'} ${Math.abs(f.avg_pl).toFixed(0)}% on entry.</p>`}`:'';
  return `<div class="i-h">${av(t.s)}<span><b>${esc(t.s)}</b><small>${esc(D.names[t.s]||'')}</small></span><a href="${D.gt}${D.addr[t.s]}" target="_blank" rel="noopener">Chart ↗</a></div>
   <div class="i-k"><div><span>Holder strength</span><b>${t.score.toFixed(0)}</b></div><div><span>${HD[S.win]}</span><b>${d==null?'—':Math.abs(d)<0.5?'0':(d>0?'+':'−')+Math.abs(d).toFixed(0)}</b></div><div><span>Price, ${WL[S.win]}</span><b>${pct(t.chg[S.win])}</b></div></div>
   ${SOB.ascii(t.score)}
   ${t.moved[S.win]?`<p class="i-s">${esc(t.moved[S.win])}</p>`:''}
   <div class="pt">${parts}</div>${st}
   <div class="det-f"><button type="button" class="wbtn" data-w="${t.s}">${w?'★ On your watchlist':'☆ Watch'}</button><a class="wbtn" href="${D.links.flows}">Flows</a><a class="wbtn" href="${D.links.smart}">Smart money</a></div>`}
 function render(){
  const R=D.read[S.win];$('h-h').textContent=R.h;$('h-p').innerHTML=R.p;
  SOB.seg($('h-win'),D.wins.map(w=>[w,WL[w]]),S.win,v=>{S.win=v;S.all=false;render()});
  SOB.seg($('h-sort'),[['top','Firmest'],['low','Weakest'],['move','Moved most']],S.sort,v=>{S.sort=v;S.all=false;S.open=null;render()});
  const items=[...D.tokens].sort((a,b)=>S.sort==='top'?b.score-a.score:S.sort==='low'?a.score-b.score:Math.abs(b.d[S.win]||0)-Math.abs(a.d[S.win]||0));
  if(!items.find(t=>t.s===S.open))S.open=items.length&&!narrow.matches?items[0].s:null;
  const shown=S.all?items:items.slice(0,TOP);
  $('h-rows').innerHTML=shown.map((t,i)=>row(t,i+1)).join('')+(items.length>TOP?`<li style="border:0"><button type="button" class="all" data-all="1">${S.all?'Show top '+TOP:'Show all '+items.length}</button></li>`:'');
  const t=D.tokens.find(t=>t.s===S.open);$('h-insp').innerHTML=t?detail(t):'<p class="empty">Pick a token to see its four parts.</p>';
  SOB.fit(root);
 }
 root.addEventListener('click',e=>{
  const w=e.target.closest('[data-w]');if(w){SOB.watch.toggle(w.dataset.w);render();return}
  if(e.target.closest('[data-all]')){S.all=!S.all;render();return}
  const r=e.target.closest('.row');if(r){S.open=(S.open===r.dataset.s&&narrow.matches)?null:r.dataset.s;render()}
 });
 narrow.addEventListener('change',render);
 window.addEventListener('resize',()=>SOB.fit(root));
 window.addEventListener('sob:view',e=>{if(e.detail==='holders')SOB.fit(root)});
 render();
})();
"""


def views(cache, href, hm=None):
    return [view(model(cache), href)]


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    m = model(sys.argv[1])
    hp = os.path.join(sys.argv[1], "holders", "history.json")        # a daily score history, for trend lines later
    hist = json.load(open(hp)) if os.path.exists(hp) else {}
    hist[m["asof"]] = {t["symbol"]: t["now"]["score"] for t in m["tokens"]}
    json.dump(hist, open(hp, "w"), separators=(",", ":"))
    page = SH.page(view(m), f"Data through {dshort(m['asof'])}", m["meta"])
    tmp = sys.argv[2] + ".tmp"
    open(tmp, "w", encoding="utf-8").write(page); os.replace(tmp, sys.argv[2])
    r = read(m)
    print("holders:", r["h"], f"{len(m['tokens'])} tokens through {m['asof']}; windows {', '.join(m['wins'])}")
    for t in sorted(m["tokens"], key=lambda t: -t["now"]["score"]):
        print(f"  {t['symbol']:9s} {t['now']['score']:5.1f} (wk {t['week_ago']['score']:5.1f}) | {t['moved']['7d']}")


if __name__ == "__main__":
    main()
