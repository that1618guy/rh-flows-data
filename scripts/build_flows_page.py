"""The Flows page: who is buying and selling each Robinhood Chain token, and which flows stand out.

Crypto tokens only (stock tokens have their own page), and only tokens with $50K+ of pool liquidity whose volume
isn't passed through throwaway wallets (inflow.passed_through). Net = USD
bought less USD sold by wallets in the token's main pools; bots are shown apart. One ranked list (bought or sold),
ten rows with the rest behind a button, and the picked token's detail beside it. A token with no net flow for the
chosen wallet group isn't listed.

Usage: build_flows_page.py <cache_dir> <out.html>
       cache_dir is data/flows_cache (token day files, tokens_meta.json). `views()` gives the parts for a bundle.
"""
import html, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows_model as FM
import inflow as IN
import site_shell as SH
import token_meta as TM
from site_shell import fm, signed, pct, dshort

MIN_TOKENS = 8                 # fewer than this on a run means something broke: keep the last good page
EVEN = 0.05                    # net within this % of the tokens' combined market cap reads as even
# What makes a flow stand out. Sizes are a share of the token's market cap, so small tokens are judged fairly.
SPLIT_MIN = 0.05               # whales' net, % of market cap, to flag whales going against the crowd
SPLIT_USD = 10_000             # and in dollars, so a $5K whale trade in a tiny token doesn't count
SPLIT_OPP = 0.25               # the crowd must take at least this share of the other side
DIVERGE_NET = 0.10             # net flow, % of market cap, to flag a flow going against the price
DIVERGE_PX = 5.0               # price move, %, in the opposite direction
STREAK = 4                     # days in a row
TURN_USD = 10_000              # the latest day's net, against the window's direction, that earns a line in the read
FRESH_N, FRESH_SHARE = 15, 0.5  # new wallets, and their share of the net buying
GROUPS = ["whale", "big", "mid", "small"]
GLABEL = {"all": "All wallets", "whale": "Whales", "big": "Big", "mid": "Mid", "small": "Small"}
GLONG = {"whale": "Whales", "big": "Big wallets", "mid": "Mid wallets", "small": "Small wallets"}


def daily_net(tdir):
    """[[day, wallets' net USD]] for every cached day, bots left out by the same rule the model uses."""
    t = FM.load_token(tdir)
    kinds = t["head"]["kinds"]
    out = []
    for d in sorted(t["days"]):
        net = 0.0
        for a, row in t["days"][d]["rows"].items():
            n = row[2]
            if n > FM.BOT_TRADES_PER_DAY or (kinds.get(a, "eoa") not in FM.WALLET_KINDS and n >= 10):
                continue
            net += row[0] - row[1]
        out.append([d, round(net)])
    return out


def streak(daily):
    """Days in a row, ending on the latest day, with net buying (+n) or net selling (-n)."""
    n = 0
    for _, v in reversed(daily):
        s = 1 if v > 1 else -1 if v < -1 else 0
        if s == 0 or (n and (s > 0) != (n > 0)):
            break
        n += s
    return n


def tags(w, size, run):
    """Short labels for what stands out in one token's window, strongest first: [(id, label, sentence)]."""
    out = []
    net = w["all"]["net"]; wh = w["whale"]["net"]; crowd = w["mid"]["net"] + w["small"]["net"]
    if size:
        if abs(wh) / size * 100 >= SPLIT_MIN and abs(wh) >= SPLIT_USD and wh * crowd < 0 and abs(crowd) >= SPLIT_OPP * abs(wh):
            if wh > 0:
                out.append(("split", "Whales buying, crowd selling", f"Whales bought {fm(wh)} while smaller wallets sold {fm(crowd)}."))
            else:
                out.append(("split", "Whales selling, crowd buying", f"Whales sold {fm(wh)} while smaller wallets bought {fm(crowd)}."))
        chg = w.get("price_chg")
        if chg is not None and abs(net) / size * 100 >= DIVERGE_NET:
            if net > 0 and chg <= -DIVERGE_PX:
                out.append(("weak", "Bought on weakness", f"Wallets bought {fm(net)} while the price fell {abs(chg):.0f}%."))
            elif net < 0 and chg >= DIVERGE_PX:
                out.append(("strong", "Sold into strength", f"Wallets sold {fm(net)} while the price rose {chg:.0f}%."))
    fr = w["all"]
    if fr["fresh_n"] >= FRESH_N and net > 0 and fr["fresh_net"] >= FRESH_SHARE * net:
        out.append(("fresh", "New wallets leading", f"{fr['fresh_n']:,} new wallets bought {fm(fr['fresh_net'])} and still hold it."))
    if abs(run) >= STREAK:
        out.append(("run", f"{'Bought' if run > 0 else 'Sold'} {abs(run)} days running", f"Wallets have net {'bought' if run > 0 else 'sold'} it {abs(run)} days in a row."))
    return out


RANK = {"split": 3, "weak": 3, "strong": 3, "fresh": 2, "run": 1}      # what leads "Worth a look"


def read(T, wid, win, sizes, day=None):
    """Headline and at most three sentences for one window. day is the latest day's window, for the longer ones."""
    N = len(T)
    g = {k: sum(t["w"][wid][k]["net"] for t in T) for k in GROUPS}
    tot = sum(g.values())
    total_size = sum(sizes.values()) or 1
    share = tot / total_size * 100
    h = ("Wallets are buying RH tokens." if share > EVEN else "Wallets are selling RH tokens." if share < -EVEN
         else "Buying and selling are close to even.")
    when = f"on {dshort(win['to'])}" if wid == "1d" else f"in the 7 days to {dshort(win['to'])}" if wid == "7d" else f"in the 30 days to {dshort(win['to'])}"
    p = f"Wallets net {'bought' if tot >= 0 else 'sold'} <b>{fm(tot)}</b> across {N} tokens {when}."
    sign = 1 if tot >= 0 else -1
    lead = max(g, key=lambda k: sign * g[k]); opp = max(g, key=lambda k: -sign * g[k])
    if abs(g[lead]) > 1000:
        p += f" {GLONG[lead]} led the {'buying' if tot >= 0 else 'selling'} ({signed(g[lead])})"
        if -sign * g[opp] > max(10_000, 0.15 * abs(tot)):
            p += f", while {GLONG[opp].lower()} {'bought' if g[opp] > 0 else 'sold'} {fm(g[opp])}"
        p += "."
    up = sum(1 for t in T if t["w"][wid]["all"]["net"] > 1)
    turn = ""
    if wid != "1d" and day:                      # the latest day went against the window: say so, it's the news
        d_tot = sum(t["w"]["1d"]["all"]["net"] for t in T)
        d_up = sum(1 for t in T if t["w"]["1d"]["all"]["net"] > 1)
        if d_tot * tot < 0 and abs(d_tot) > TURN_USD:
            turn = (f" {dshort(day['to'])} went the other way: wallets net {'bought' if d_tot > 0 else 'sold'} "
                    f"<b>{fm(d_tot)}</b>, with {d_up} of {N} tokens net bought.")
    p += turn or f" {up} of {N} tokens were net bought."
    return {"h": h, "p": p}


def model(cache_dir):
    flows = FM.build(cache_dir)
    meta = json.load(open(os.path.join(cache_dir, "tokens_meta.json")))
    passed = IN.passed_through(flows)
    T = [t for t in flows["tokens"] if t["category"] != "stocks" and t["symbol"] not in passed
         and (meta.get(t["symbol"]) or {}).get("liq", 0) >= IN.MIN_LIQ]
    sizes = {t["symbol"]: TM.size(meta[t["symbol"]]) for t in T}
    wins = {}
    for wid, w in flows["windows"].items():
        wins[wid] = {"from": w["from"], "to": w["to"],
                     "label": dshort(w["to"]) if wid == "1d" else f"{dshort(w['from'])} to {dshort(w['to'])}"}
    toks, look = [], {wid: [] for wid in wins}
    for t in T:
        s = t["symbol"]; size = sizes[s]
        daily = daily_net(os.path.join(cache_dir, s))[-30:]
        run = streak(daily)
        rec = {"s": s, "size": round(size), "daily": daily, "run": run, "w": {}}
        for wid in wins:
            w = t["w"][wid]
            tg = tags(w, size, run)
            rec["w"][wid] = {"net": {k: round(w[k]["net"]) for k in ["all"] + GROUPS},
                             "buyers": w["all"]["buyers"], "sellers": w["all"]["sellers"],
                             "fresh_n": w["all"]["fresh_n"], "fresh_net": round(w["all"]["fresh_net"]),
                             "bots": round(w["bots"]["net"]), "chg": w["price_chg"], "tags": [[i, l, x] for i, l, x in tg]}
            if tg and size:
                usd = abs(w["whale"]["net"]) if tg[0][0] == "split" else abs(w["all"]["net"])
                look[wid].append((RANK[tg[0][0]], usd, s, tg[0][1], tg[0][2]))
        toks.append(rec)
    cohorts = {}
    for wid in wins:
        c = {}
        for k in GROUPS:
            c[k] = {"net": round(sum(t["w"][wid][k]["net"] for t in T)),
                    "up": sum(1 for t in T if t["w"][wid][k]["net"] > 1), "down": sum(1 for t in T if t["w"][wid][k]["net"] < -1)}
        c["fresh"] = {"n": sum(t["w"][wid]["all"]["fresh_n"] for t in T), "net": round(sum(t["w"][wid]["all"]["fresh_net"] for t in T))}
        c["bots"] = {"n": sum(t["w"][wid]["bots"]["wallets"] for t in T), "net": round(sum(t["w"][wid]["bots"]["net"] for t in T))}
        cohorts[wid] = c
    return {"asof": flows["asof"], "windows": wins, "tokens": toks, "cohorts": cohorts, "meta": meta,
            "read": {wid: read(T, wid, flows["windows"][wid], sizes, flows["windows"].get("1d")) for wid in wins},
            "look": {wid: [{"s": s, "label": l, "text": x} for _, _, s, l, x in sorted(v, reverse=True)[:3]] for wid, v in look.items()},
            "left_out": sorted(t["symbol"] for t in flows["tokens"]
                               if t["category"] != "stocks" and t not in T and t["symbol"] not in passed),
            "passed": sorted(passed), "pass_share": IN.pass_share(flows),
            "stale": flows["stale"], "unpriced": [x for x in flows["unpriced"] if x in meta], "incomplete": flows["incomplete"]}


def view(m, href=lambda i: f"/{i}"):
    data = {k: m[k] for k in ("asof", "windows", "tokens", "cohorts", "read", "look")}
    data["groups"] = [[k, GLABEL[k]] for k in ["all"] + GROUPS]
    data["ranges"] = FM.GROUP_RANGE
    data["glong"] = GLONG
    data["gt"] = "https://www.geckoterminal.com/robinhood/tokens/"
    data["addr"] = {s: x["address"] for s, x in m["meta"].items()}
    data["names"] = {s: x["name"] for s, x in m["meta"].items()}
    data["links"] = {"smart": href("smart-money"), "holders": href("holders")}
    left = f" Left out for thin liquidity: {', '.join(m['left_out'])}." if m["left_out"] else ""
    if m["passed"]:
        left += f" Left out with {IN.PASS_NOTE}: {', '.join(m['passed'])}."
    if m["unpriced"]:
        left += f" No recent price: {', '.join(m['unpriced'])}."
    body = f"""
<section class="pg-head">
<p class="eyebrow">Flows · <span id="f-when"></span></p>
<h1 id="f-h"></h1>
<p class="lede" id="f-p"></p>
<div class="ctl pg-ctl"><div class="seg" id="f-win" role="group" aria-label="Window"></div><div class="seg" id="f-grp" role="group" aria-label="Wallet size"></div><button type="button" class="chip" id="f-watch" hidden></button></div>
</section>

<section class="sec" id="f-look-sec">
<div class="sec-h"><div><h2>Worth a look</h2><p>Flows that go against the price or against the crowd.</p></div></div>
<div class="look" id="f-look"></div>
</section>

<section class="sec">
<div class="md">
<div class="md-list"><div class="sec-h"><div class="seg" id="f-side" role="group" aria-label="Bought or sold"></div><p class="md-sum" id="f-sum"></p></div>
<div class="panel"><ol class="rank" id="f-rows"></ol></div></div>
<aside class="panel insp" id="f-insp" aria-live="polite"></aside>
</div>
</section>

<section class="sec">
<div class="sec-h"><div><h2>Who is behind it</h2><p>The same flows by wallet size, added across every token.</p></div></div>
<div class="coh4" id="f-coh"></div>
<p class="note" id="f-apart" style="margin-top:14px"></p>
</section>

<details class="how"><summary>How this is measured</summary><dl>
<dt>Net flow</dt><dd>USD bought less USD sold by wallets in each token's main pools, read swap by swap from Robinhood Chain and priced with GeckoTerminal. The share of market cap beside it lets you compare a small token with a large one.</dd>
<dt>Wallet size</dt><dd>By what a wallet held of that token at the start or end of the window, whichever is larger: whales $50K+, big $5K to $50K, mid $500 to $5K, small under $500.</dd>
<dt>New wallets</dt><dd>Held none at the start, net bought, and still hold at least half of it.</dd>
<dt>Bots</dt><dd>Addresses trading more than 100 times a day, and contracts trading 10+ times a day. Counted apart, never inside a wallet group.</dd>
<dt>Worth a look</dt><dd>Whales against the crowd: whales' net is at least {SPLIT_MIN}% of market cap and $10K, and smaller wallets took a quarter or more of the other side. Bought on weakness, sold into strength: net flow of {DIVERGE_NET:.1f}%+ of market cap against a {DIVERGE_PX:.0f}%+ price move. Days running: {STREAK}+ days in a row. New wallets leading: {FRESH_N}+ new wallets did half or more of the net buying.</dd>
<dt>Included</dt><dd>Crypto tokens on Robinhood Chain with $50K+ of pool liquidity.{left} Stock tokens have their own page.</dd>
</dl></details>
"""
    return {"id": "flows", "title": "Flows", "desc": "Who is buying and selling each Robinhood Chain token.",
            "body": body, "css": CSS, "js": JS, "data": data}


CSS = r"""
.dly svg{display:block;width:100%;height:48px;margin-top:10px}
.coh4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.ch{padding:20px 20px 18px;border-left:1px solid var(--line);min-width:0}
.ch:first-child{border-left:0;padding-left:0}
.ch-l{font-size:14.5px;font-weight:500}
.ch-l small{color:var(--ink-3);font-weight:400;margin-left:6px;font-size:12.5px}
.ch-v{margin:8px 0 12px;font-size:26px;font-weight:620;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.ch small.c{display:block;margin-top:10px;color:var(--ink-3);font-size:12.5px}
@media (max-width:860px){
  .coh4{grid-template-columns:1fr 1fr}
  .ch{padding:16px 14px}
  .ch:nth-child(odd){border-left:0;padding-left:0}
  .ch:nth-child(n+3){border-top:1px solid var(--line)}
}
"""

JS = r"""
(function(){
 const root=document.querySelector('[data-view="flows"]');if(!root)return;
 const D=JSON.parse(document.getElementById('flows-data').textContent),$=id=>root.querySelector('#'+id);
 const {fm,signed,pct,esc,av}=SOB,G=['whale','big','mid','small'];
 const S={win:D.windows['7d']?'7d':'1d',grp:'all',side:null,open:null,all:false,watch:false};
 const TOP=10,WL={'1d':'1D','7d':'7D','30d':'30D'},narrow=window.matchMedia('(max-width:860px)');
 const net=t=>t.w[S.win].net[S.grp];
 function row(t,i,max){
  const w=t.w[S.win],v=net(t),of=t.size?Math.abs(v)/t.size*100:null,tag=w.tags[0],on=S.open===t.s,inl=on&&narrow.matches;
  const star=SOB.watch.get().includes(t.s)?'<span class="star" title="On your watchlist">★</span>':'';
  return `<li class="${on?'sel':''}"><button type="button" class="row${on?' sel':''}" data-s="${t.s}" aria-pressed="${on}"><span class="n">${i}</span>${av(t.s)}
   <span><span class="tks">${star}${esc(t.s)}</span><span class="sub${tag?' tg':''}">${esc(tag?tag[1]:D.names[t.s]||t.s)}</span></span>
   <span class="bar${v<0?' dn':''}"><i style="width:${Math.max(Math.abs(v)/max*100,2).toFixed(0)}%"></i></span>
   <span class="val">${signed(v)}${of!=null?`<small>${of<0.005?'&lt;0.01':of.toFixed(2)}% of market cap</small>`:''}</span></button>${inl?`<div class="inl">${detail(t)}</div>`:''}</li>`}
 function detail(t){
  const w=t.w[S.win],max=Math.max(1,...G.map(g=>Math.abs(w.net[g])));
  const cg=G.map(g=>{const v=w.net[g],p=Math.abs(v)/max*50;
   return `<span>${D.glong[g]}</span><span class="zb"><i class="${v>=0?'u':'d'}" style="width:${p.toFixed(1)}%"></i></span><b>${signed(v)}</b>`}).join('');
  const d=t.daily.slice(-14),dm=Math.max(1,...d.map(x=>Math.abs(x[1]))),bw=100/d.length;
  const bars=d.map((x,i)=>{const h=Math.abs(x[1])/dm*22;return `<rect x="${(i*bw+bw*.14).toFixed(2)}" y="${x[1]>=0?(24-h).toFixed(2):24}" width="${(bw*.72).toFixed(2)}" height="${Math.max(h,.6).toFixed(2)}" rx=".7" fill="var(--${x[1]>=0?'up':'down'})"><title>${SOB.day(x[0])}: ${signed(x[1])}</title></rect>`}).join('');
  const watched=SOB.watch.get().includes(t.s);
  return `<div class="i-h">${av(t.s)}<span><b>${esc(t.s)}</b><small>${esc(D.names[t.s]||'')}</small></span><a href="${D.gt}${D.addr[t.s]}" target="_blank" rel="noopener">Chart ↗</a></div>
   <div class="i-k"><div><span>Net, all wallets</span><b>${signed(w.net.all)}</b></div><div><span>Price, ${WL[S.win]}</span><b>${pct(w.chg)}</b></div><div><span>Market cap</span><b>${fm(t.size)}</b></div></div>
   ${w.tags.map(x=>`<p class="i-s">${esc(x[2])}</p>`).join('')}
   <p class="i-t">By wallet size</p><div class="cg">${cg}</div>
   <div class="facts"><span><b>${w.buyers.toLocaleString()}</b> buyers</span><span><b>${w.sellers.toLocaleString()}</b> sellers</span><span><b>${w.fresh_n.toLocaleString()}</b> new wallets${w.fresh_n?` (${signed(w.fresh_net)})`:''}</span><span>Bots <b>${signed(w.bots)}</b></span></div>
   <p class="i-t">Net flow per day, ${SOB.day(d[0][0])} to ${SOB.day(d[d.length-1][0])}</p>
   <div class="dly"><svg viewBox="0 0 100 48" preserveAspectRatio="none" role="img" aria-label="Net flow per day">${bars}<line x1="0" x2="100" y1="24" y2="24" stroke="var(--line-2)" stroke-width=".4"/></svg></div>
   <div class="det-f"><button type="button" class="wbtn" data-w="${t.s}">${watched?'★ On your watchlist':'☆ Watch'}</button><a class="wbtn" href="${D.links.holders}">Holders</a><a class="wbtn" href="${D.links.smart}">Smart money</a></div>`}
 function render(){
  SOB.seg($('f-win'),Object.keys(D.windows).map(w=>[w,WL[w]]),S.win,v=>{S.win=v;S.side=null;render()});
  SOB.seg($('f-grp'),D.groups,S.grp,v=>{S.grp=v;S.side=null;render()});
  const W=D.windows[S.win],R=D.read[S.win],wl=SOB.watch.get();
  $('f-when').textContent=W.label;$('f-h').textContent=R.h;$('f-p').innerHTML=R.p;
  const wb=$('f-watch');wb.hidden=!wl.length;wb.textContent=`★ Watchlist (${wl.length})`;if(!wl.length)S.watch=false;wb.classList.toggle('on',S.watch);
  const T=D.tokens.filter(t=>!S.watch||wl.includes(t.s));
  const buy=T.filter(t=>net(t)>1).sort((a,b)=>net(b)-net(a)),sell=T.filter(t=>net(t)<-1).sort((a,b)=>net(a)-net(b));
  const sb=buy.reduce((a,t)=>a+net(t),0),ss=sell.reduce((a,t)=>a+net(t),0);
  if(!S.side){S.side=(-ss>sb)?'sell':'buy';S.stay=false}
  if(!S.stay&&S.side==='buy'&&!buy.length&&sell.length)S.side='sell';
  if(!S.stay&&S.side==='sell'&&!sell.length&&buy.length)S.side='buy';
  SOB.seg($('f-side'),[['buy',`Bought · ${buy.length}`],['sell',`Sold · ${sell.length}`]],S.side,v=>{S.side=v;S.stay=true;S.all=false;S.open=null;render()});
  const items=S.side==='buy'?buy:sell,who=S.grp==='all'?'all wallets':S.grp==='whale'?'whales':D.glong[S.grp].toLowerCase();
  $('f-sum').textContent=(items.length?`${signed(S.side==='buy'?sb:ss)} by ${who}${S.grp==='all'?'':' ('+D.ranges[S.grp]+')'}`:`By ${who}`)+` · ${W.label}`;
  if(!items.find(t=>t.s===S.open))S.open=items.length&&!narrow.matches?items[0].s:null;
  if(S.open&&items.findIndex(t=>t.s===S.open)>=TOP)S.all=true;
  const max=items.length?Math.max(...items.map(t=>Math.abs(net(t)))):1,shown=S.all?items:items.slice(0,TOP);
  $('f-rows').innerHTML=items.length?shown.map((t,i)=>row(t,i+1,max)).join('')+(items.length>TOP?`<li style="border:0"><button type="button" class="all" data-all="1">${S.all?'Show top '+TOP:'Show all '+items.length}</button></li>`:'')
   :`<li class="empty">No token was net ${S.side==='buy'?'bought':'sold'} by ${who} in this window. Try another window or wallet size at the top of the page.</li>`;
  const insp=$('f-insp'),t=D.tokens.find(t=>t.s===S.open);
  insp.innerHTML=t?detail(t):'<p class="empty">Pick a token to see who is behind its flow.</p>';
  const L=D.look[S.win]||[];$('f-look-sec').hidden=!L.length;
  $('f-look').innerHTML=L.map(x=>`<button type="button" class="lk" data-s="${x.s}"><span class="lk-t">${av(x.s,'s')}${esc(x.s)}</span><span class="lk-l">${esc(x.label)}</span><span class="lk-x">${esc(x.text)}</span></button>`).join('');
  const C=D.cohorts[S.win],cm=Math.max(1,...G.map(g=>Math.abs(C[g].net)));
  $('f-coh').innerHTML=G.map(g=>{const c=C[g],p=Math.abs(c.net)/cm*50;
   return `<div class="ch"><div class="ch-l">${D.glong[g]}<small>${D.ranges[g]}</small></div><div class="ch-v">${signed(c.net)}</div><div class="zb"><i class="${c.net>=0?'u':'d'}" style="width:${p.toFixed(1)}%"></i></div><small class="c">Bought more in ${c.up}, sold more in ${c.down}</small></div>`}).join('');
  $('f-apart').textContent=`New wallets: ${C.fresh.n.toLocaleString()} bought ${fm(C.fresh.net)} and still hold it. Bots and contracts: ${signed(C.bots.net)} across ${C.bots.n.toLocaleString()} addresses, counted apart.`;
 }
 root.addEventListener('click',e=>{
  const w=e.target.closest('[data-w]');if(w){SOB.watch.toggle(w.dataset.w);render();return}
  if(e.target.closest('[data-all]')){S.all=!S.all;render();return}
  const lk=e.target.closest('.lk');if(lk){const t=D.tokens.find(t=>t.s===lk.dataset.s);S.grp='all';S.side=t.w[S.win].net.all>=0?'buy':'sell';S.open=t.s;render();
   const r=root.querySelector(`.row[data-s="${t.s}"]`);if(r)r.scrollIntoView({block:'center',behavior:'smooth'});return}
  const r=e.target.closest('.row');if(r){S.open=(S.open===r.dataset.s&&narrow.matches)?null:r.dataset.s;render();return}
  if(e.target.closest('#f-watch')){S.watch=!S.watch;S.side=null;render()}
 });
 narrow.addEventListener('change',render);
 render();
})();
"""


def views(cache, href, hm=None):
    return [view(model(cache), href)]


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    cache, out = sys.argv[1], sys.argv[2]
    try:
        m = model(cache)
        if len(m["tokens"]) < MIN_TOKENS:
            raise RuntimeError(f"only {len(m['tokens'])} tokens")
    except Exception as e:
        if os.path.exists(out):
            print(f"::error title=Flows::kept the last good page ({e})"); return
        raise
    page = SH.page(view(m), f"Data through {dshort(m['asof'])}", m["meta"])
    tmp = out + ".tmp"
    open(tmp, "w", encoding="utf-8").write(page); os.replace(tmp, out)
    r = m["read"]["7d" if "7d" in m["read"] else "1d"]
    print(f"flows: {len(m['tokens'])} tokens through {m['asof']}. {r['h']}")
    ps = m["pass_share"]; kept = [s for s in ps if s not in m["passed"]]
    top = max(kept, key=ps.get) if kept else None
    print("flows: pass-through volume: " + (", ".join(f"{s} {ps[s]:.0f}% (left out)" for s in m["passed"]) or "none left out")
          + (f"; highest kept {top} {ps[top]:.0f}%" if top else ""))


if __name__ == "__main__":
    main()
