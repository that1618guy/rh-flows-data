"""The Smart money page: what the most profitable wallets on Robinhood Chain hold, and what they did yesterday.

  the book       what they still hold of what they bought since the window opened, token by token, with how many
                 of them are in it and how they stand against their average entry
  yesterday      what they bought and sold on the latest day, and how many of them were on each side
  against        tokens where they went the other way from every other wallet
  the wallets    the ten best, with the rest behind a button

Crypto tokens with $50K+ of pool liquidity only. Built from the flows cache by smart_money.py; no chain reads.

Usage: build_smart_page.py <cache_dir> <out.html>
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inflow as IN
import site_shell as SH
import smart_money as SM
from site_shell import fm, signed, pct, dshort

QUIET = 5_000            # their net on the day, in dollars, under which the headline says they were quiet
AGAINST_MIN = 1_000


def model(cache_dir):
    meta = json.load(open(os.path.join(cache_dir, "tokens_meta.json")))
    passed = IN.passed_through_in(cache_dir)
    syms = {s for s, x in meta.items() if x.get("liq", 0) >= IN.MIN_LIQ and s not in passed}
    m = SM.build(cache_dir, symbols=syms)
    m["meta"] = meta; m["passed"] = sorted(passed)
    return m


def read(m):
    T = m["tokens"]
    if not m["n_smart"]:
        return {"h": "No wallet qualified this window.", "p": "No wallet met the bar for the selection window. Back tomorrow."}
    net = sum(t["smart"]["net"] for t in T); crowd = sum(t["crowd"]["net"] for t in T)
    day = dshort(m["day"])
    h = "Smart money is buying." if net > QUIET else "Smart money is selling." if net < -QUIET else "Smart money is quiet."
    p = (f"The {m['n_smart']} most profitable wallets of {dshort(m['sel']['from'])} to {dshort(m['sel']['to'])} net "
         f"{'bought' if net >= 0 else 'sold'} <b>{fm(net)}</b> on {day}")
    top = max(T, key=lambda t: abs(t["smart"]["net"]))
    if abs(top["smart"]["net"]) > 1000:
        p += f", mostly {top['symbol']} ({signed(top['smart']['net'])})"
    p += f", while everyone else net {'bought' if crowd >= 0 else 'sold'} {fm(crowd)}."
    book = sorted((t for t in T if t["book"]["usd"] > 0), key=lambda t: -t["book"]["usd"])
    if book:
        b = book[0]; tot = sum(t["book"]["usd"] for t in book)
        p += f" They still hold <b>{fm(tot)}</b> of what they bought in that window, most of it in {b['symbol']}"
        p += f", where they are {'up' if b['book']['pl'] >= 0 else 'down'} {abs(b['book']['pl']):.0f}% on entry." if b["book"]["pl"] is not None else "."
    return {"h": h, "p": p}


def against(m):
    out = []
    for t in m["tokens"]:
        s, c = t["smart"]["net"], t["crowd"]["net"]
        if abs(s) >= AGAINST_MIN and s * c < 0:
            out.append((abs(s), {"s": t["symbol"], "label": f"{t['smart']['buyers']} buying, {t['smart']['sellers']} selling",
                                 "text": f"They {'bought' if s > 0 else 'sold'} {fm(s)} while everyone else {'bought' if c > 0 else 'sold'} {fm(c)}."}))
    return [x for _, x in sorted(out, key=lambda x: -x[0])[:3]]


def view(m, href=lambda i: f"/{i}"):
    T = []
    for t in m["tokens"]:
        b = t["book"]
        T.append({"s": t["symbol"], "usd": round(b["usd"]), "wallets": b["wallets"], "pl": b["pl"], "top": b["top"],
                  "net": round(t["smart"]["net"]), "buyers": t["smart"]["buyers"], "sellers": t["smart"]["sellers"],
                  "crowd": round(t["crowd"]["net"]), "chg": t["chg"]})
    data = {"day": m["day"], "sel": m["sel"], "n": m["n_smart"], "active": m["active"], "tokens": T, "board": m["board"],
            "read": read(m), "against": against(m),
            "names": {s: x["name"] for s, x in m["meta"].items()}, "addr": {s: x["address"] for s, x in m["meta"].items()},
            "gt": "https://www.geckoterminal.com/robinhood/tokens/", "links": {"flows": href("flows"), "holders": href("holders")}}
    c = m["criteria"]
    left = f" Left out with {IN.PASS_NOTE}: {', '.join(m['passed'])}." if m.get("passed") else ""
    body = f"""
<section class="pg-head">
<p class="eyebrow">Smart money · {dshort(m['day'])} <span class="nb">· updated daily</span></p>
<h1 id="s-h"></h1>
<p class="lede" id="s-p"></p>
</section>

<section class="sec" id="s-ag-sec">
<div class="sec-h"><div><h2>Against the crowd</h2><p>Where they went the other way from every other wallet on {dshort(m['day'])}.</p></div></div>
<div class="look" id="s-ag"></div>
</section>

<section class="sec">
<div class="md">
<div class="md-list"><div class="sec-h"><div class="seg" id="s-side" role="group" aria-label="View"></div><p class="md-sum" id="s-sum"></p></div>
<div class="panel"><ol class="rank" id="s-rows"></ol></div></div>
<aside class="panel insp" id="s-insp" aria-live="polite"></aside>
</div>
</section>

<section class="sec">
<div class="sec-h"><div><h2>The wallets</h2><p>Ranked by trading result, {dshort(m['sel']['from'])} to {dshort(m['sel']['to'])}. Tap an address to copy it.</p></div></div>
<div class="scroll"><table class="tbl" id="s-board"></table></div>
<button type="button" class="all" id="s-more" hidden></button>
</section>

<details class="how"><summary>How this is measured</summary><dl>
<dt>Who counts</dt><dd>Up to {SM.N_SMART} wallets with the best trading result over every day we have before the latest one (up to 29). Each needs ${SM.MIN_RESULT}+ of result, ${c['min_vol']:,}+ traded, and {c['min_trades']}+ trades on {c['min_days']}+ days. Bots and contracts are left out. The latest day is kept out of the ranking, so what they bought is never the reason they were picked.</dd>
<dt>Trading result</dt><dd>Only on tokens a wallet bought inside the window: what it bought and sold, at the average sell price less the average buy price, plus what it still holds, marked to the last close. Selling old holdings or tokens it was sent never counts as profit.</dd>
<dt>The book</dt><dd>What those wallets still hold of what they bought since the window opened, at the latest close. Positions under ${SM.BOOK_MIN_USD} aren't counted. On entry is the latest price against the average price they paid.</dd>
<dt>Included</dt><dd>Crypto tokens on Robinhood Chain with $50K+ of pool liquidity.{left} Stock tokens have their own page.</dd>
</dl></details>
"""
    return {"id": "smart-money", "title": "Smart money", "desc": "What the most profitable wallets on Robinhood Chain hold and trade.",
            "body": body, "css": CSS, "js": JS, "data": data}


CSS = r"""
.pos{list-style:none;margin:10px 0 0;padding:0;font-size:13.5px}
.pos li{display:grid;grid-template-columns:minmax(0,1fr) auto 64px;gap:12px;padding:7px 0;border-top:1px solid var(--line)}
.pos li:first-child{border-top:0}
.pos b{font-weight:550;text-align:right;font-variant-numeric:tabular-nums}
.pos span:last-child{text-align:right;color:var(--ink-2);font-variant-numeric:tabular-nums}
.adr{border:0;background:none;padding:0;color:var(--ink-2);font:500 12.5px var(--mono);cursor:pointer;text-align:left}
.adr:hover{color:var(--ink)}
#s-board .adr{font-size:13px}
#s-board td small{color:var(--ink-3);margin-left:6px}
"""

JS = r"""
(function(){
 const root=document.querySelector('[data-view="smart-money"]');if(!root)return;
 const D=JSON.parse(document.getElementById('smart-money-data').textContent),$=id=>root.querySelector('#'+id);
 const {fm,signed,pct,esc,av}=SOB,narrow=window.matchMedia('(max-width:860px)');
 const S={side:'book',open:null,all:false,more:false},TOP=10;
 const sh=a=>a.slice(0,6)+'…'+a.slice(-4),adr=a=>`<button type="button" class="adr" data-a="${a}" title="${a}">${sh(a)}</button>`;
 const val=t=>S.side==='book'?t.usd:t.net;
 function row(t,i,max){
  const v=val(t),on=S.open===t.s,inl=on&&narrow.matches,star=SOB.watch.get().includes(t.s)?'<span class="star">★</span>':'';
  const sub=S.side==='book'?`${t.wallets} wallet${t.wallets===1?'':'s'}${t.pl==null?'':' · '+(t.pl>=0?'up ':'down ')+Math.abs(t.pl).toFixed(0)+'% on entry'}`:`${t.buyers} buying · ${t.sellers} selling`;
  const small=S.side==='book'?(Math.abs(t.net)>1?`${signed(t.net)} on the day`:'no trades on the day'):(t.usd?`hold ${fm(t.usd)}`:'');
  return `<li class="${on?'sel':''}"><button type="button" class="row" data-s="${t.s}" aria-pressed="${on}"><span class="n">${i}</span>${av(t.s)}
   <span><span class="tks">${star}${esc(t.s)}</span><span class="sub tg">${sub}</span></span>
   <span class="bar${S.side==='book'?' nt':v<0?' dn':''}"><i style="width:${Math.max(Math.abs(v)/max*100,2).toFixed(0)}%"></i></span>
   <span class="val">${S.side==='book'?fm(v):signed(v)}<small>${small}</small></span></button>${inl?`<div class="inl">${detail(t)}</div>`:''}</li>`}
 function detail(t){
  const day=SOB.day(D.day),w=SOB.watch.get().includes(t.s);
  const s1=Math.abs(t.net)>1?`On ${day} they net ${t.net>=0?'bought':'sold'} ${fm(t.net)} (${t.buyers} buying, ${t.sellers} selling) while everyone else ${t.crowd>=0?'bought':'sold'} ${fm(t.crowd)}.`:`None of them traded it on ${day}; everyone else ${t.crowd>=0?'bought':'sold'} ${fm(t.crowd)}.`;
  const pos=t.top.length?`<p class="i-t">Biggest positions</p><ul class="pos">${t.top.map(x=>`<li>${adr(x[0])}<b>${fm(x[1])}</b><span>${pct(x[2],0)}</span></li>`).join('')}</ul>`:'';
  return `<div class="i-h">${av(t.s)}<span><b>${esc(t.s)}</b><small>${esc(D.names[t.s]||'')}</small></span><a href="${D.gt}${D.addr[t.s]}" target="_blank" rel="noopener">Chart ↗</a></div>
   <div class="i-k"><div><span>They hold</span><b>${fm(t.usd)}</b></div><div><span>On entry</span><b>${pct(t.pl,0)}</b></div><div><span>Wallets in it</span><b>${t.wallets} of ${D.n}</b></div></div>
   <p class="i-s">${s1}</p>${pos}
   <div class="det-f"><button type="button" class="wbtn" data-w="${t.s}">${w?'★ On your watchlist':'☆ Watch'}</button><a class="wbtn" href="${D.links.flows}">Flows</a><a class="wbtn" href="${D.links.holders}">Holders</a></div>`}
 function render(){
  $('s-h').textContent=D.read.h;$('s-p').innerHTML=D.read.p;
  const book=D.tokens.filter(t=>t.usd>0).sort((a,b)=>b.usd-a.usd),buy=D.tokens.filter(t=>t.net>1).sort((a,b)=>b.net-a.net),sell=D.tokens.filter(t=>t.net<-1).sort((a,b)=>a.net-b.net);
  SOB.seg($('s-side'),[['book',`Their book · ${book.length}`],['buy',`Bought · ${buy.length}`],['sell',`Sold · ${sell.length}`]],S.side,v=>{S.side=v;S.all=false;S.open=null;render()});
  const items=S.side==='book'?book:S.side==='buy'?buy:sell,sum=items.reduce((a,t)=>a+val(t),0);
  $('s-sum').textContent=!items.length?'':S.side==='book'?`${fm(sum)} held since ${SOB.day(D.sel.from)}`:`${signed(sum)} on ${SOB.day(D.day)}`;
  if(!items.find(t=>t.s===S.open))S.open=items.length&&!narrow.matches?items[0].s:null;
  const max=items.length?Math.max(...items.map(t=>Math.abs(val(t)))):1,shown=S.all?items:items.slice(0,TOP);
  $('s-rows').innerHTML=items.length?shown.map((t,i)=>row(t,i+1,max)).join('')+(items.length>TOP?`<li style="border:0"><button type="button" class="all" data-all="1">${S.all?'Show top '+TOP:'Show all '+items.length}</button></li>`:'')
   :`<li class="empty">${S.side==='book'?'They hold nothing bought in this window.':'They '+(S.side==='buy'?'bought':'sold')+' nothing on '+SOB.day(D.day)+'.'}</li>`;
  const t=D.tokens.find(t=>t.s===S.open);$('s-insp').innerHTML=t?detail(t):'<p class="empty">Pick a token to see who is in it.</p>';
  $('s-ag-sec').hidden=!D.against.length;
  $('s-ag').innerHTML=D.against.map(x=>`<button type="button" class="lk" data-s="${x.s}"><span class="lk-t">${av(x.s,'s')}${esc(x.s)}</span><span class="lk-l">${esc(x.label)}</span><span class="lk-x">${esc(x.text)}</span></button>`).join('');
  const B=S.more?D.board:D.board.slice(0,TOP);
  $('s-board').innerHTML=`<tr><th>Wallet</th><th>Result</th><th>Traded</th><th>Tokens won</th><th>Best token</th><th>On ${SOB.day(D.day)}</th></tr>`+B.map((w,i)=>`<tr><td><span class="mut" style="display:inline-block;width:28px">${i+1}</span>${adr(w.a)}</td><td><b>${signed(w.res)}</b></td><td>${fm(w.vol)}</td><td>${w.wins} of ${w.ntok}</td><td>${w.best?esc(w.best)+`<small>${signed(w.best_res)}</small>`:'—'}</td><td>${w.day_top?signed(w.day_net)+`<small>${esc(w.day_top)}</small>`:'<span class="mut">no trades</span>'}</td></tr>`).join('');
  const mb=$('s-more');mb.hidden=D.board.length<=TOP;mb.textContent=S.more?'Show top '+TOP:'Show all '+D.board.length;
 }
 root.addEventListener('click',e=>{
  const a=e.target.closest('.adr');if(a){const t=a.textContent;try{navigator.clipboard.writeText(a.dataset.a).then(()=>{a.textContent='Copied';setTimeout(()=>a.textContent=t,900)},()=>{})}catch(x){};e.stopPropagation();return}
  const w=e.target.closest('[data-w]');if(w){SOB.watch.toggle(w.dataset.w);render();return}
  if(e.target.closest('[data-all]')){S.all=!S.all;render();return}
  if(e.target.closest('#s-more')){S.more=!S.more;render();return}
  const lk=e.target.closest('.lk');if(lk){const t=D.tokens.find(t=>t.s===lk.dataset.s);S.side=t.net>=0?'buy':'sell';S.open=t.s;render();const r=root.querySelector(`.row[data-s="${t.s}"]`);if(r)r.scrollIntoView({block:'center',behavior:'smooth'});return}
  const r=e.target.closest('.row');if(r){S.open=(S.open===r.dataset.s&&narrow.matches)?null:r.dataset.s;render()}
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
    m = model(sys.argv[1])
    page = SH.page(view(m), f"Data through {dshort(m['asof'])}", m["meta"])
    tmp = sys.argv[2] + ".tmp"
    open(tmp, "w", encoding="utf-8").write(page); os.replace(tmp, sys.argv[2])
    print("smart money:", read(m)["h"], f"{m['n_smart']} wallets, {m['active']} active on {m['day']}")


if __name__ == "__main__":
    main()
