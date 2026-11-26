"""The Smart Filter page: the tokens worth a look, day by day, and what price did after each one.

  the read        how many tokens are flagged today and for what
  the tape        every day as a cell with one mark per flagged token; picking a day shows its flags, and a switch
                  narrows everything to one filter
  the flags       one ticket per token: the three figures behind the flag, a price trace with the day marked, and
                  what price has done since against the median token
  the record      beside the flags: how often each live filter went its way. Filters on trial sit below.

Built from the Smart Filter log (smart_filter.py), which this brings up to date first. No chain reads.

Usage: build_smart_filter_page.py <cache_dir> <out.html>
"""
import json, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inflow as IN
import smart_filter as SU
import site_shell as SH
from site_shell import fm, dshort

SHOW_DAYS = 7                    # days listed before "Show earlier days"
WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six"]
AS = {"exit": "an exit", "smart": "smart money leaving", "washout": "a washout", "runup": "a run-up"}
MEANS = {"exit": "The big holders are leaving.", "smart": "The most profitable wallets are selling.",
         "washout": "It sold off hard."}
WHAT = {
    "exit": "Large wallets sold over 7 days and the 50 largest holders cut their bags.",
    "smart": "Several of the most profitable wallets sold on the day, and few of them bought.",
    "runup": "Up 20%+ in 3 days with new wallets or large wallets buying into it.",
    "washout": "Down 20%+ on the day, with who sold and who bought.",
    "accum": "Large wallets bought over 3 days while price stayed flat and smaller wallets sold.",
    "distrib": "Large wallets sold over 3 days while smaller wallets bought.",
    "smartbuy": "Several of the most profitable wallets bought on the day, and few of them sold.",
    "bigbuy": "Large wallets bought twice a big amount in one day.",
    "bigsell": "Large wallets sold twice a big amount in one day.",
}


def _join(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def since(it, D):
    """What price has done since a flag: the outcome at the kind's horizon once it is in, the move so far before."""
    n = SU.KINDS[it["kind"]][2]
    if it.get(f"r{n}") is not None:
        return {"done": True, "n": n, "r": it[f"r{n}"], "m": it[f"m{n}"]}
    k = SU._gap(it["d"], D["asof"])
    if k < 1:
        return None
    def move(sym):
        c = D["F"].get(sym) or {}                    # a token flagged earlier may have left the list since
        a, b = (c.get(it["d"]) or {}).get("close"), (c.get(D["asof"]) or {}).get("close")
        return (b / a - 1) * 100 if a and b else None
    r, ms = move(it["s"]), [x for x in (move(s) for s in D["shown"]) if x is not None]
    if r is None or len(ms) < 5:
        return None
    return {"done": False, "n": n, "k": k, "r": round(r, 1), "m": round(statistics.median(ms), 1)}


def read(log, rec):
    asof = log["asof"]
    today = [it for it in log["items"] if it["d"] == asof and it["posted"]]
    live = [SU.KINDS[k][0].lower() for k in SU.ORDER if SU.KINDS[k][4]]
    tail = " Each one says what flags like it have led to so far."
    if not today:
        last = next((it for it in reversed(log["items"]) if it["posted"]), None)
        p = f"No token showed {_join([AS[k] for k in SU.ORDER if SU.KINDS[k][4]])} on {dshort(asof)}."
        if last:
            p += f" The last was {last['s']}, {AS[last['kind']]}, on {dshort(last['d'])}."
        return {"h": "Nothing passes the filter today.", "p": p + tail}
    parts = []
    for k in SU.ORDER:
        syms = [it["s"] for it in today if it["kind"] == k]
        if syms:
            parts.append(f"<b>{_join(syms)}</b> for {AS[k]}")
    h = f"{WORDS[len(today)] if len(today) < len(WORDS) else len(today)} token{'' if len(today) == 1 else 's'} to watch."
    return {"h": h, "p": _join(parts) + "." + tail}


TRACE_BEFORE, TRACE_AFTER = 14, 7


def trace(it, D):
    """Closes around a flag for the small price chart: up to 14 days before it and 7 after, and where the flag sits."""
    c = D["F"].get(it["s"]) or {}                # a token flagged earlier may have left the list since
    days = [d for d in SU.span(SU.shift(it["d"], TRACE_AFTER), TRACE_BEFORE + TRACE_AFTER + 1) if d in c and d <= D["asof"]]
    if len(days) < 3 or it["d"] not in days:
        return None
    return {"p": [c[d]["close"] for d in days], "i": days.index(it["d"]), "from": dshort(days[0]), "to": dshort(days[-1])}


def model(cache_dir):
    log, D = SU.update(cache_dir)
    meta = json.load(open(os.path.join(cache_dir, "tokens_meta.json")))
    return {"log": log, "D": D, "rec": SU.record(log), "meta": meta, "passed": sorted(IN.passed_through_in(cache_dir))}


def view(m, href=lambda i: f"/{i}"):
    log, D, rec = m["log"], m["D"], m["rec"]
    days = []
    for d in log["days"]:
        items = [{"s": it["s"], "kind": it["kind"], "kinds": it.get("kinds") or [it["kind"]], "figs": it.get("figs") or [],
                  "note": it.get("note") or "", "also": it.get("also") or [], "since": since(it, D), "trace": trace(it, D)}
                 for it in log["items"] if it["d"] == d and it["posted"]]
        cut = {}
        for it in log["items"]:
            if it["d"] == d and it.get("cut"):
                cut.setdefault(SU.KINDS[it["kind"]][0].lower(), []).append(it["s"])
        days.append({"d": d, "label": dshort(d), "items": items,
                     "more": "; ".join(f"{', '.join(v)} ({k})" for k, v in cut.items())})
    kinds = {k: {"name": n, "side": side, "days": nd, "posted": p, "what": WHAT[k], "means": MEANS.get(k, "")}
             for k, (n, side, nd, _q, p) in SU.KINDS.items()}
    r = read(log, rec)
    data = {"asof": log["asof"], "from": log["days"][0], "days": days, "kinds": kinds, "order": SU.ORDER, "rec": rec,
            "min": SU.MIN_RECORD, "names": {s: x["name"] for s, x in m["meta"].items()}, "addr": {s: x["address"] for s, x in m["meta"].items()},
            "gt": "https://www.geckoterminal.com/robinhood/tokens/"}
    left = f" Left out with {IN.PASS_NOTE}: {', '.join(m['passed'])}." if m.get("passed") else ""
    body = f"""
<section class="pg-head">
<p class="eyebrow">Smart Filter · {dshort(log['asof'])} <span class="nb">· updated daily</span></p>
<h1>{r['h']}</h1>
<p class="lede">{r['p']}</p>
</section>

<section class="sec sf">
<div class="sec-h sf-top"><div class="seg" id="sf-kind" role="group" aria-label="Filter"></div><p class="md-sum" id="sf-sum"></p></div>
<div class="sf-tape" id="sf-tape" role="group" aria-label="Day"></div>
<div class="sf-cols">
<div class="sf-main">
<div class="sf-dayh"><h2 id="sf-day"></h2><p id="sf-n"></p><span class="sf-step"><button type="button" class="wbtn" id="sf-prev" aria-label="The day before">‹</button><button type="button" class="wbtn" id="sf-next" aria-label="The day after">›</button></span></div>
<div id="sf-flags"></div>
<div class="sf-led" id="sf-led"></div>
</div>
<aside class="panel sf-rec" id="sf-rec" aria-label="How each filter has done"></aside>
</div>
</section>

<section class="sec">
<div class="sec-h"><div><h2>On trial</h2><p>Filters that are logged and scored the same way but never posted. One moves up when its record earns it.</p></div></div>
<div class="sf-trial" id="sf-trial"></div>
</section>

<details class="how"><summary>How this is measured</summary><dl>
<dt>What it does</dt><dd>It runs every token through a few filters on what its wallets are doing and lists the ones that pass. Each token gets one line a day at most, the same story isn't repeated on the days after, and a day lists {SU.MAX_DAY} at most. On some days nothing passes.</dd>
<dt>Exit</dt><dd>Large wallets (holding $5K+ of the token) net sold a big amount over 7 days, and the 50 largest holders of a week ago cut their combined balance by {abs(SU.EXIT_TOP):.0f}% or more. A big amount is {SU.MIN_CAP}% of market cap, never under {fm(SU.MIN_USD)} or over {fm(SU.MAX_USD)}; an exit needs {SU.EXIT_LARGE} of them.</dd>
<dt>Smart money leaving</dt><dd>The most profitable wallets, ranked only on the days before, net sold {fm(SU.SMART_USD)}+ on the day, with at least {SU.SMART_MIN} of them selling and at least {SU.SMART_EDGE} more selling than buying.</dd>
<dt>Washout</dt><dd>Price down {abs(SU.WASH_PCT):.0f}%+ on the day. The ticket says what large wallets and new wallets did into it, and whether the 50 largest holders have been cutting.</dd>
<dt>What happened after</dt><dd>The token's close on the day it was flagged against its close 7 days later (3 days for a washout). The median token is the middle result of every token on these pages over the same days, so a flag is judged against the pack, not against zero.</dd>
<dt>The record</dt><dd>It starts on {dshort(log['days'][0])} and is still short, so read every figure as early. None of these is a proven edge.</dd>
<dt>Included</dt><dd>Crypto tokens on Robinhood Chain with $50K+ of pool liquidity.{left} Data, not advice.</dd>
</dl></details>
"""
    return {"id": "smart-filter", "title": "Smart Filter", "desc": "The Robinhood Chain tokens worth a look today, and what price did after each flag.",
            "body": body, "css": CSS, "js": JS, "data": data}


CSS = r"""
.sf-top{align-items:center;margin-bottom:0}
/* the tape: one cell a day, one mark per flagged token */
.sf-tape{display:flex;align-items:stretch;margin-top:18px;padding:22px 0 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);overflow-x:auto;scrollbar-width:none}
.sf-tape::-webkit-scrollbar{display:none}
.sf-dc{position:relative;flex:1 0 34px;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:8px;height:104px;padding:0 0 12px;border:0;border-radius:10px 10px 0 0;background:none;color:var(--soft2);cursor:pointer}
.sf-dc:hover{color:var(--text)}
.sf-pips{display:flex;flex-direction:column-reverse;align-items:center;gap:3px;font:14px/11px var(--mono)}
.sf-pips i{font-style:normal}
.sf-dc.has .sf-pips{color:var(--soft)}
.sf-dn{font:500 11.5px var(--mono);font-variant-numeric:tabular-nums}
.sf-dc.on{background:var(--raised);color:var(--text)}
.sf-dc.on .sf-pips{color:var(--lime-ink)}
.sf-mo{position:absolute;top:-17px;left:50%;transform:translateX(-50%);font:500 10.5px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--soft2);white-space:nowrap}
/* the day's flags beside the record */
.sf-cols{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:28px;align-items:start;margin-top:28px}
.sf-dayh{display:flex;align-items:baseline;gap:14px;margin-bottom:16px}
.sf-dayh p{color:var(--soft);font-size:14.5px}
.sf-step{margin-left:auto;display:flex;gap:6px}
.sf-step .wbtn{width:34px;height:34px;padding:0;justify-content:center;font-size:18px;line-height:1}
.sf-step .wbtn:disabled{opacity:.35;cursor:default}
.sf-t{padding:20px 22px 20px}
.sf-t+.sf-t{margin-top:14px}
.sf-t .i-h b{display:flex;align-items:center;gap:10px}
.sf-t .i-h .tag{font:500 12px var(--font);letter-spacing:0;padding:2px 9px;color:var(--text)}
.sf-out{margin-left:auto;text-align:right;font:600 20px/1.2 var(--tight);letter-spacing:-.02em;font-variant-numeric:tabular-nums;white-space:nowrap}
.sf-t .i-h .sf-out b{display:inline;font:inherit}
.sf-out span{font:500 13.5px var(--font);letter-spacing:0;color:var(--soft)}
.sf-out small{display:block;margin-top:3px;font:400 12.5px var(--font);letter-spacing:0;color:var(--soft2)}
.sf-out.new{font:500 13px var(--font);letter-spacing:0;color:var(--soft2)}
.sf-mid{display:grid;grid-template-columns:minmax(0,1fr) 216px;gap:24px;align-items:stretch;margin-top:14px}
.sf-t .i-k{margin-top:0}
.sf-t .i-k b{font-size:24px}
.sf-tr{display:flex;flex-direction:column;justify-content:center;gap:6px;min-width:0}
.sf-ch{position:relative;height:56px}
.sf-tr svg{display:block;width:100%;height:56px;overflow:visible}
.sf-dot{position:absolute;width:9px;height:9px;margin:-4.5px 0 0 -4.5px;border-radius:50%;background:var(--panel);border:1.5px solid var(--text)}
.sf-tr small{color:var(--soft2);font-size:12px}
.sf-t .i-s{margin-top:14px;color:var(--soft)}
.sf-say{margin-top:14px;font:600 17px/1.3 var(--tight);letter-spacing:-.01em}
.sf-so{margin-top:16px;padding-top:14px;border-top:1px solid var(--line);font-size:14.5px;line-height:1.5;text-wrap:pretty}
.sf-so span{display:block;margin-bottom:3px;color:var(--soft2);font-size:12.5px}
.sf-empty{padding:34px 0 30px;border-top:1px solid var(--line);color:var(--soft);font-size:15px}
.sf-empty span{display:block;margin-top:4px;color:var(--soft2);font-size:13.5px}
.sf-also{margin-top:14px;color:var(--soft2);font-size:13.5px}
/* earlier flags: a plain ledger under the tickets */
.sf-led{margin-top:34px}
.sf-led h3{font:600 17px var(--tight);letter-spacing:-.01em;margin-bottom:6px}
.sf-lr{display:grid;grid-template-columns:58px 24px minmax(0,1fr) minmax(0,1fr) 220px;gap:12px;align-items:center;width:100%;padding:11px 10px;margin:0 -10px;width:calc(100% + 20px);border:0;border-top:1px solid var(--line);background:none;color:var(--text);font:inherit;font-size:14px;text-align:left;cursor:pointer}
.sf-lr:hover{background:var(--hover)}
.sf-lr>span:first-child{color:var(--soft2);font:500 12px var(--mono)}
.sf-lr b{font:600 14.5px var(--tight)}
.sf-lr .k{color:var(--soft)}
.sf-lr .o{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.sf-lr .o small{color:var(--soft2);margin-left:8px;font-size:12.5px}
/* the record */
.sf-rec{position:sticky;top:calc(env(safe-area-inset-top,0px) + 88px);padding:22px 24px}
.sf-rec h3{font:600 17px var(--tight);letter-spacing:-.01em}
.sf-ri{margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}
.sf-ri>b{display:block;font-weight:600;font-size:15px}
.sf-m{display:block;margin:9px 0 8px;font:13px/1 var(--mono);letter-spacing:1.5px;color:var(--text);white-space:nowrap;overflow:hidden}
.sf-m i{font-style:normal;color:var(--soft2);opacity:.55}
.sf-ri p{font-size:14px;line-height:1.4}
.sf-ri p b{font-weight:600}
.sf-ri small{display:block;margin-top:4px;color:var(--soft2);font-size:12.5px;line-height:1.4}
.sf-rec .note{margin-top:18px;padding-top:14px;border-top:1px solid var(--line)}
.sf-trial{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 36px}
.sf-trial .sf-ri{margin-top:0;padding:16px 0 18px}
@media (max-width:900px){
 .sf-tape{margin-left:-20px;margin-right:-20px;padding-left:12px;padding-right:12px}
 .sf-dc{flex:0 0 38px}
 .sf-cols{grid-template-columns:1fr;gap:36px}
 .sf-rec{position:static}
 .sf-t{padding:16px}
 .sf-mid{grid-template-columns:1fr;gap:14px}
 .sf-t .i-k div{padding:11px 0 10px 10px}
 .sf-t .i-k b{font-size:20px}
 .sf-t .i-k span{white-space:normal;line-height:1.25;min-height:2.5em}
 .sf-t .i-h{flex-wrap:wrap}
 .sf-t .i-h>span:nth-child(2){flex:1;min-width:0}
 .sf-t .i-h b{flex-wrap:wrap;gap:6px 10px}
 .sf-out{flex:0 0 100%;margin:10px 0 0;text-align:left;font-size:18px}
 .sf-out small{display:inline;margin-left:8px}
 .sf-out.new{margin-top:8px}
 .sf-trial{grid-template-columns:1fr}
 .sf-lr{grid-template-columns:50px 24px minmax(0,1fr) 120px;gap:10px}
 .sf-lr .k{display:none}
 .sf-lr .o small{display:none}
}
"""

JS = r"""
(function(){
 const root=document.querySelector('[data-view="smart-filter"]');if(!root)return;
 const D=JSON.parse(document.getElementById('smart-filter-data').textContent),$=id=>root.querySelector('#'+id);
 const {esc,av}=SOB,pct=(x,d)=>Math.round(x)===0?'0%':SOB.pct(x,d);
 const live=D.order.filter(k=>D.kinds[k].posted),trial=D.order.filter(k=>!D.kinds[k].posted);
 const S={kind:'all',day:D.asof},idx=d=>D.days.findIndex(x=>x.d===d);
 const has=(i,k)=>k==='all'||i.kinds.includes(k),of=d=>d.items.filter(i=>has(i,S.kind));
 const MO=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
 function meter(r){const n=20,f=r.n?Math.round(r.hit/r.n*n):0;return `<span class="sf-m" aria-hidden="true">${'█'.repeat(f)}<i>${'░'.repeat(n-f)}</i></span>`}
 function rec(k,def){const K=D.kinds[k],r=D.rec[k],way=K.side<0?'did worse':'did better';
  return `<div class="sf-ri"><b>${esc(K.name)}</b>${meter(r)}<p>${r.n?`<b>${r.hit} of ${r.n}</b> ${way} than the median token over ${K.days} days`:'Nothing judged yet'}</p>
   <small>${r.n?`${pct(r.r,0)} on average, median token ${pct(r.m,0)}.`:''}${def?' '+esc(K.what):''}</small></div>`}
 function line(t){
  const p=t.p,lo=Math.min(...p),hi=Math.max(...p),W=216,H=56,x=i=>(i/(p.length-1)*W).toFixed(1),y=v=>(H-4-(hi>lo?(v-lo)/(hi-lo):.5)*(H-8)).toFixed(1);
  const path=(a,b)=>p.slice(a,b+1).map((v,j)=>(j?'L':'M')+x(a+j)+' '+y(v)).join('');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Price from ${t.from} to ${t.to}">
   <path d="${path(0,t.i)}" fill="none" stroke="var(--soft2)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
   ${t.i<p.length-1?`<path d="${path(t.i,p.length-1)}" fill="none" stroke="var(--text)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`:''}
   </svg><i class="sf-dot" style="left:${(t.i/(p.length-1)*100).toFixed(1)}%;top:${y(p[t.i])}px"></i>`}
 function out(x){
  if(!x)return '<span class="sf-out new">Flagged today</span>';
  const c=x.r<0?'dn':'up';
  return `<span class="sf-out"><b class="${c}">${pct(x.r,0)}</b> <span>${x.done?'in '+x.n+' days':'so far'}</span><small>median token ${pct(x.m,0)}${x.done?'':' · day '+x.k+' of '+x.n}</small></span>`}
 function ticket(it){
  const note=it.note,byName=n=>D.order.find(k=>D.kinds[k].name.toLowerCase()===n);
  const led=(k,first)=>{const K=D.kinds[k],r=D.rec[k],way=K.side<0?'did worse':'did better';
   if(r.n<D.min)return first?`Too few ${K.name.toLowerCase()} flags have played out to say yet.`:'';
   const tail=`${r.hit} of ${r.n} ${way} than the median token over the next ${K.days} days (${pct(r.r,0)} on average, against ${pct(r.m,0)})`;
   return first?`So far, ${tail}.`:` It also passes the ${K.name.toLowerCase()} filter: ${tail}.`};
  const so=led(it.kind,true)+it.also.map(byName).filter(Boolean).map(k=>led(k,false)).join('');
  return `<article class="panel sf-t"><div class="i-h">${av(it.s)}<span><b>${esc(it.s)}<span class="tag">${esc(D.kinds[it.kind].name)}</span></b><small>${esc(D.names[it.s]||'')}</small></span>${out(it.since)}</div>
   <p class="sf-say">${esc(D.kinds[it.kind].means)}</p>
   <div class="sf-mid"><div class="i-k">${it.figs.map(f=>`<div><span>${esc(f[1])}</span><b>${esc(f[0])}</b></div>`).join('')}</div>
   ${it.trace?`<div class="sf-tr"><div class="sf-ch">${line(it.trace)}</div><small>Price, ${it.trace.from} to ${it.trace.to}</small></div>`:''}</div>
   ${note?`<p class="i-s">${esc(note)}</p>`:''}
   <p class="sf-so"><span>What flags like this have led to</span>${esc(so)}</p></article>`}
 function render(){
  const count=k=>D.days.reduce((a,d)=>a+d.items.filter(i=>has(i,k)).length,0);
  SOB.seg($('sf-kind'),[['all',`All · ${count('all')}`]].concat(live.map(k=>[k,`${D.kinds[k].name} · ${count(k)}`])),S.kind,v=>{S.kind=v;render()});
  $('sf-sum').textContent=`${D.days.length} days, since ${SOB.day(D.from)}`;
  $('sf-tape').innerHTML=D.days.map((d,i)=>{const n=of(d).length,first=i===0||d.d.slice(5,7)!==D.days[i-1].d.slice(5,7);
   return `<button type="button" class="sf-dc${n?' has':''}${d.d===S.day?' on':''}" data-d="${d.d}" aria-pressed="${d.d===S.day}" aria-label="${d.label}: ${n} flagged">${first?`<span class="sf-mo">${MO[+d.d.slice(5,7)-1]}</span>`:''}
    <span class="sf-pips">${n?'<i>■</i>'.repeat(n):'<i>·</i>'}</span><span class="sf-dn">${+d.d.slice(8,10)}</span></button>`}).join('');
  const i=idx(S.day),d=D.days[i],items=of(d);
  $('sf-day').textContent=d.label;$('sf-n').textContent=items.length?`${items.length} flagged`:'';
  $('sf-prev').disabled=i<=0;$('sf-next').disabled=i>=D.days.length-1;
  $('sf-flags').innerHTML=items.length?items.map(ticket).join('')+(d.more&&S.kind==='all'?`<p class="sf-also">Also flagged, past the day's limit of ${items.length}: ${esc(d.more)}.</p>`:'')
   :`<p class="sf-empty">Nothing passed ${S.kind==='all'?'the filter':'this filter'} on ${esc(d.label)}.<span>Pick another day on the tape above.</span></p>`;
  const before=[];for(let j=i-1;j>=0&&before.length<8;j--)of(D.days[j]).forEach(it=>{if(before.length<8)before.push([D.days[j],it])});
  $('sf-led').innerHTML=before.length?`<h3>Flagged before ${esc(d.label)}</h3>`+before.map(([x,it])=>{const o=it.since;
   return `<button type="button" class="sf-lr" data-d="${x.d}"><span>${esc(x.label)}</span>${av(it.s,'s')}<b>${esc(it.s)}</b><span class="k">${esc(D.kinds[it.kind].name)}</span>
    <span class="o">${o?`<span class="${o.r<0?'dn':'up'}">${pct(o.r,0)}</span> ${o.done?'in '+o.n+' days':'so far'}<small>median ${pct(o.m,0)}</small>`:''}</span></button>`}).join(''):'';
 }
 $('sf-rec').innerHTML='<h3>How each filter has done</h3>'+live.map(k=>rec(k,false)).join('')+`<p class="note">${SOB.day(D.from)} to ${SOB.day(D.asof)}. A short record: none of these is a proven edge yet.</p>`;
 $('sf-trial').innerHTML=trial.map(k=>rec(k,true)).join('');
 root.addEventListener('click',e=>{
  const c=e.target.closest('.sf-dc,.sf-lr');if(c){const led=c.classList.contains('sf-lr');S.day=c.dataset.d;render();if(led){const el=root.querySelector('.sf-dc.on');if(el)el.scrollIntoView({block:'nearest',inline:'nearest'});$('sf-day').scrollIntoView({block:'center',behavior:'smooth'})}return}
  const step=e.target.closest('#sf-prev')?-1:e.target.closest('#sf-next')?1:0;
  if(step){const j=idx(S.day)+step;if(j>=0&&j<D.days.length){S.day=D.days[j].d;render();const el=root.querySelector('.sf-dc.on');if(el)el.scrollIntoView({block:'nearest',inline:'nearest'})}}
 });
 render();
 const tape=$('sf-tape');tape.scrollLeft=tape.scrollWidth;
})();
"""


def views(cache, href, hm=None):
    return [view(model(cache), href)]


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    m = model(sys.argv[1])
    page = SH.page(view(m), f"Data through {dshort(m['log']['asof'])}", m["meta"])
    tmp = sys.argv[2] + ".tmp"
    open(tmp, "w", encoding="utf-8").write(page); os.replace(tmp, sys.argv[2])
    log, rec = m["log"], m["rec"]
    n = sum(1 for it in log["items"] if it["d"] == log["asof"] and it["posted"])
    print(f"smart filter: {read(log, rec)['h']} {n} on {log['asof']}, {sum(1 for it in log['items'] if it['posted'])} since {log['days'][0]}; "
          + "; ".join(f"{SU.KINDS[k][0].lower()} {rec[k]['hit']} of {rec[k]['n']}" for k in SU.ORDER if SU.KINDS[k][4]))


if __name__ == "__main__":
    main()
