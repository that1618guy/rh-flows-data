"""Render the Flows page content from the flows cache: who's buying and selling Robinhood Chain tokens, by wallet size.
Only the content and its own CSS (scoped to .fl, colors from the site's tokens): build_v4.py wraps it in the site's
shared header, content column and footer when the site is built. Self-contained otherwise: inline CSS, JS and data.

Usage: build_flows_page.py <cache_dir> <out.html> [--note="..."] [--model=out.json]

Checks run before the page is written. If one fails, the existing <out.html> (the last good page) is left in place
and the run exits 0 with GitHub Actions error annotations; with no earlier page to fall back on it exits 1.
Warnings (data older than yesterday, tokens left out) are printed as annotations and don't block the page."""
import datetime as dt, json, math, sys, os, html
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows_model as FM

NOTE = "The list is reviewed by hand. HOOD is left out: it copies the stock ticker but isn't a stock token."
MIN_TOKENS = 20          # fewer tokens than this on a run means something broke: keep the last good page


def fm(x):
    a = abs(x)
    s = f"${a/1e9:.2f}B" if a >= 1e9 else f"${a/1e6:.1f}M" if a >= 1e6 else f"${a/1e3:.0f}K" if a >= 1e4 else f"${a/1e3:.1f}K" if a >= 1e3 else f"${a:,.0f}"
    return s


def signed(x):
    return ("+" if x >= 0 else "−") + fm(x)


def phrase(model, wid):
    d = FM.dshort(model["asof"])
    return f"on {d}" if wid == "1d" else f"in the {wid[:-1]} days to {d}"


def window_label(model, wid):
    w = model["windows"][wid]
    return FM.dshort(w["to"]) if wid == "1d" else f"{FM.dshort(w['from'])} to {FM.dshort(w['to'])}"


def read(model, wid):
    """Two to three plain sentences on the window, every number from the model."""
    T = model["tokens"]; N = len(T)
    g = {gid: sum(t["w"][wid][gid]["net"] for t in T) for gid in ("whale", "big", "mid", "small")}
    total = sum(g.values())
    lab = {x["id"]: x["label"] for x in model["groups"]}
    verb = "bought" if total >= 0 else "sold"
    s1 = f"Wallets net {verb} {fm(total)} of the {N} Robinhood Chain token{'s' if N != 1 else ''} we track {phrase(model, wid)}."
    sign = 1 if total >= 0 else -1
    lead = max(g, key=lambda k: sign * g[k])
    opp = max(g, key=lambda k: -sign * g[k])
    s2 = f" {lab[lead]} led the {'buying' if total >= 0 else 'selling'} ({signed(g[lead])})"
    if -sign * g[opp] > max(25_000, 0.15 * abs(total)):
        s2 += f", while {lab[opp].lower()} {'bought' if g[opp] > 0 else 'sold'} {fm(g[opp])}"
    s2 += "."
    cats = {c["id"]: [t for t in T if t["category"] == c["id"]] for c in model["cats"]}
    cnet = {c: sum(t["w"][wid]["all"]["net"] for t in ts) for c, ts in cats.items() if ts}
    labc = {c["id"]: c["label"] for c in model["cats"]}
    s3 = ""
    against = [c for c in cnet if (cnet[c] > 0) != (total > 0) and abs(cnet[c]) > 10_000]
    if against:
        c = max(against, key=lambda c: abs(cnet[c]))
        top = max(cats[c], key=lambda t: -sign * t["w"][wid]["all"]["net"])
        s3 = (f" {labc[c]} went the other way ({signed(cnet[c])}), led by {top['symbol']} "
              f"({signed(top['w'][wid]['all']['net'])}).")
    else:
        top = max(T, key=lambda t: sign * t["w"][wid]["all"]["net"])
        s3 = f" {top['symbol']} saw the most {'buying' if total >= 0 else 'selling'} ({signed(top['w'][wid]['all']['net'])})."
    return s1 + s2 + s3


def stock_read(model, wid):
    """Supply change, then who traded the stock in the pools: wallets vs bots."""
    S = [t for t in model["tokens"] if t["category"] == "stocks"]
    if not S:
        return ""
    minted = sum(t["w"][wid]["minted"] for t in S)
    dex = sum(t["w"][wid]["all"]["net"] for t in S)
    bots = sum(t["w"][wid]["bots"]["net"] for t in S)
    a = f"Stock tokens' supply on the chain {'grew' if minted >= 0 else 'shrank'} by {fm(minted)} {phrase(model, wid)}."
    b = (f" In the pools, wallets net {'bought' if dex >= 0 else 'sold'} {fm(dex)}, while bots, mostly arbitrage desks, "
         f"net {'bought' if bots >= 0 else 'sold'} {fm(bots)}.")
    return a + b


def windows_note(model):
    have = model["windows"]
    s = "1D is the latest full UTC day" + (", 7D the latest seven" if "7d" in have else "") + \
        (", 30D the latest thirty" if "30d" in have else "") + "."
    later = [w for w in ("7d", "30d") if w not in have]
    if later:
        s += (f" {' and '.join(w.upper() for w in later)} {'appear' if len(later) > 1 else 'appears'} once "
              f"{' and '.join(w[:-1] for w in later)} days are recorded.")
    return s


def not_shown(model):
    parts = []
    if model.get("unpriced"):
        parts.append(f"{', '.join(model['unpriced'])} (no price data)")
    if model.get("stale"):
        parts.append(f"{', '.join(model['stale'])} (didn't update this run)")
    if model.get("incomplete"):
        parts.append(f"{', '.join(model['incomplete'])} (history still filling in)")
    return f" Not shown today: {'; '.join(parts)}." if parts else ""


def check(model):
    """Hard problems (errs) keep the last good page up; warnings are reported but the page still updates."""
    errs, warns = [], []
    T = model["tokens"]
    if len(T) < MIN_TOKENS:
        errs.append(f"only {len(T)} tokens made it through (need {MIN_TOKENS}+)")
    if "1d" not in model["windows"]:
        errs.append("no 1D window")
    groups = ("whale", "big", "mid", "small")
    for t in T:
        if not t.get("price"):
            errs.append(f"{t['symbol']}: no price")
        for w in model["windows"]:
            r = t["w"].get(w)
            if not r:
                errs.append(f"{t['symbol']}: no {w} numbers"); continue
            vals = [r[g][k] for g in groups + ("bots", "all") for k in ("net", "bought", "sold")] + [r["minted"]]
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals):
                errs.append(f"{t['symbol']} {w}: a number isn't finite")
            elif abs(sum(r[g]["net"] for g in groups) - r["all"]["net"]) > 1:
                errs.append(f"{t['symbol']} {w}: size groups don't add up to all wallets")
    for w in model["windows"]:
        if T and not sum(t["w"][w]["all"]["bought"] + t["w"][w]["all"]["sold"] for t in T if w in t["w"]):
            errs.append(f"{w}: no volume at all")
        if not (model.get("reads") or {}).get(w):
            errs.append(f"{w}: empty read")
    yday = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
    if model["asof"] < yday:
        warns.append(f"data is through {model['asof']}, not {yday}: the chain read didn't finish this run")
    if model.get("stale"):
        warns.append(f"didn't update this run, left out: {', '.join(model['stale'])}")
    if model.get("incomplete"):
        warns.append(f"history still filling in, left out: {', '.join(model['incomplete'])}")
    if model.get("unpriced"):
        warns.append(f"no price data, left out: {', '.join(model['unpriced'])}")
    return errs[:12], warns


CSS = r"""
.fl{--card:var(--panel);--card2:color-mix(in srgb,var(--panel),var(--text) 5%);--line2:color-mix(in srgb,var(--line),var(--text) 8%);
--ink:var(--text);--faint:var(--soft2);--neg:var(--off);--warn:var(--mid);--mono:"JetBrains Mono",ui-monospace,Menlo,monospace;
line-height:1.5;padding-top:40px}
.fl .over{font:500 11px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--soft)}
.fl h1{font-size:46px;line-height:1.05;letter-spacing:-.035em;margin:14px 0 18px;font-weight:700}
.fl .read{font-size:18px;line-height:1.6;max-width:760px;margin:0}
.fl .meta{font-size:13px;color:var(--faint);margin-top:14px}
.fl .ctl{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:34px 0 18px}
.fl .seg{display:inline-flex;border:1px solid var(--line2);border-radius:999px;padding:3px;gap:2px;background:var(--card);max-width:100%;overflow-x:auto;scrollbar-width:none}
.fl .seg::-webkit-scrollbar{display:none}
.fl .seg button{all:unset;cursor:pointer;padding:6px 13px;border-radius:999px;font-size:13.5px;color:var(--soft);white-space:nowrap}
.fl .seg button:hover{color:var(--ink)}.fl .seg button.on{background:var(--lime);color:var(--on-lime);font-weight:600}
.fl .seg.sm button{padding:5px 11px;font-size:13px}
.fl .count{margin-left:auto;font:12px var(--mono);color:var(--faint)}
.fl .grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:18px}
.fl .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 24px}
.fl .card h2{font-size:19px;letter-spacing:-.02em;margin:0;font-weight:650}
.fl .card .sub{font-size:13px;color:var(--soft);margin:6px 0 0;line-height:1.5}
.fl .ch{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.fl .rank{margin-top:16px}
.fl .rrow{display:grid;grid-template-columns:22px minmax(0,150px) minmax(0,1fr) 92px;gap:12px;align-items:center;padding:7px 8px;border-radius:9px;cursor:pointer}
.fl .rrow:hover{background:var(--card2)}.fl .rrow.sel{background:var(--card2);box-shadow:inset 0 0 0 1px var(--line2)}
.fl .rrow .n{font:12px var(--mono);color:var(--faint);text-align:right}
.fl .tk b{font-weight:650;font-size:14px}.fl .tk small{display:block;color:var(--faint);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fl .bar{position:relative;height:16px}
.fl .bar:before{content:"";position:absolute;left:50%;top:-4px;bottom:-4px;border-left:1px solid var(--line2)}
.fl .bar span{position:absolute;top:2px;height:12px;border-radius:3px}
.fl .bar .p{left:50%;background:var(--lime-ink)}.fl .bar .m{right:50%;background:var(--neg)}
.fl .val{text-align:right;font-variant-numeric:tabular-nums;font-size:14px;font-weight:600}
.fl .val.p{color:var(--lime-ink)}.fl .val.m{color:var(--neg)}
.fl .more{all:unset;cursor:pointer;margin-top:12px;border:1px solid var(--line2);border-radius:10px;padding:8px 14px;font-size:13.5px}
.fl .more:hover{border-color:var(--soft)}
.fl .chip{border:1px solid var(--line2);border-radius:999px;padding:4px 12px;font-size:13px;color:var(--soft);display:inline-flex;align-items:center;gap:7px}
.fl .pt{font:600 9.5px var(--mono);font-style:normal;letter-spacing:.06em;text-transform:uppercase;color:var(--warn);border:1px solid color-mix(in srgb,var(--warn) 40%,transparent);border-radius:5px;padding:1px 5px;margin-left:7px;vertical-align:2px}
.fl .flag{font-size:13px;line-height:1.5;color:var(--warn);background:color-mix(in srgb,var(--warn) 8%,transparent);border:1px solid color-mix(in srgb,var(--warn) 25%,transparent);border-radius:10px;padding:10px 12px;margin:12px 0 0}
.fl .lead{font:11px var(--mono);color:var(--faint);letter-spacing:.06em;text-transform:uppercase}
.fl table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.fl th{font:500 11px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--faint);text-align:right;padding:0 0 10px;font-weight:500}
.fl th:not(:first-child){padding-left:10px}
.fl #stbl th{padding-left:14px;white-space:nowrap}
.fl th:first-child,.fl td:first-child{text-align:left}
.fl td{padding:11px 0;border-top:1px solid var(--line);text-align:right;font-size:14px}
.fl td:not(:first-child){white-space:nowrap;padding-left:10px}
.fl td .rg{display:block;color:var(--faint);font-size:12px;margin-top:2px;font-weight:400}
.fl .more[hidden]{display:none}
.fl tr.tot td{font-weight:650}
.fl #stbl td .rg{display:inline;margin-left:6px}
.fl tr.apart td{color:var(--soft)}
.fl td.p{color:var(--lime-ink)}.fl td.m{color:var(--neg)}
.fl .note{font-size:12.5px;color:var(--faint);line-height:1.55;margin:14px 0 0}
.fl .tdet{margin-top:22px;padding-top:20px;border-top:1px solid var(--line)}
.fl .tdet .hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.fl .tdet .hd b{font-size:17px}.fl .tdet .hd span{color:var(--soft);font-size:13px}
.fl .kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0 6px}
.fl .kpi{border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.fl .kpi b{display:block;font-size:19px;letter-spacing:-.02em}.fl .kpi span{font-size:12px;color:var(--soft)}
.fl .mini{display:grid;grid-template-columns:minmax(0,1fr) 120px 74px 62px 62px;gap:8px;align-items:center;font-size:13.5px;padding:8px 0;border-top:1px solid var(--line)}
.fl .mini.h{border-top:0;font:500 10.5px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--faint);padding-top:0}
.fl .mini>:not(:first-child){text-align:right}.fl .mini .bar{height:12px}.fl .mini .bar span{height:8px}
.fl .sec{margin-top:48px}
.fl .sec>.ch h2{font-size:26px;letter-spacing:-.03em;margin:0}
.fl .sec>.ch p{color:var(--soft);margin:8px 0 0;max-width:120ch;font-size:15px}
.fl .sread{font-size:16px;line-height:1.6;margin:16px 0 18px;max-width:900px}
.fl details.how{margin-top:48px;border-top:1px solid var(--line);padding-top:18px}
.fl details.how summary{cursor:pointer;font-weight:600;font-size:16px;list-style:none}
.fl details.how summary:after{content:" +";color:var(--soft)}.fl details.how[open] summary:after{content:" \2212"}
.fl details.how dl{display:grid;grid-template-columns:180px minmax(0,1fr);gap:10px 24px;margin:18px 0 0;font-size:14px;line-height:1.6}
.fl details.how dt{font-weight:600}.fl details.how dd{margin:0;color:var(--soft)}
.fl .fsrc{margin-top:28px;font-size:12.5px;color:var(--faint);line-height:1.6}
.fl .beta{font:600 10.5px var(--mono);letter-spacing:.12em;color:var(--lime-ink);border:1px solid color-mix(in srgb,var(--lime-ink) 35%,transparent);border-radius:6px;padding:3px 7px;margin-left:10px;vertical-align:middle}
@media (max-width:980px){.fl .grid{grid-template-columns:minmax(0,1fr)}}
@media (max-width:620px){.fl{padding-top:28px}.fl h1{font-size:34px}.fl .read{font-size:16.5px}
.fl .rrow{grid-template-columns:18px minmax(0,96px) minmax(0,1fr) 74px;gap:8px;padding:7px 4px}.fl .val{font-size:13px}
.fl .card{padding:18px 16px}.fl .kpis{grid-template-columns:1fr 1fr}.fl .mini{grid-template-columns:minmax(0,1fr) 70px 54px 44px 44px;font-size:12.5px}
.fl .count{margin-left:0;width:100%}.fl #across th:nth-child(2),.fl #across td:nth-child(2){display:none}.fl td:not(:first-child){padding-left:6px}.fl details.how dl{grid-template-columns:1fr}}
"""

JS = r"""
const M=JSON.parse(document.getElementById('flows-data').textContent);
const S={w:('7d' in M.windows)?'7d':'1d',c:'all',g:'all',dir:'buy',sel:null,all:false};
const fm=x=>{const a=Math.abs(x);return a>=1e9?'$'+(a/1e9).toFixed(2)+'B':a>=1e6?'$'+(a/1e6).toFixed(1)+'M':a>=1e4?'$'+Math.round(a/1e3)+'K':a>=1e3?'$'+(a/1e3).toFixed(1)+'K':'$'+Math.round(a).toLocaleString()};
const sg=x=>Math.abs(x)<0.5?'$0':(x>=0?'+':'−')+fm(x);
const cls=x=>x>=0?'p':'m';
const GL=Object.fromEntries(M.groups.map(g=>[g.id,g.label]));
const toks=()=>M.tokens.filter(t=>S.c==='all'||t.category===S.c);
function bar(v,max){const w=max?Math.min(50,Math.abs(v)/max*50):0;return `<div class="bar"><span class="${v>=0?'p':'m'}" style="width:${w.toFixed(2)}%"></span></div>`}
function seg(el,key,opts){el.innerHTML=opts.map(([v,l])=>`<button data-v="${v}" class="${S[key]===v?'on':''}">${l}</button>`).join('');
 el.onclick=e=>{const b=e.target.closest('button');if(!b)return;S[key]=b.dataset.v;if(key==='c'){S.sel=null;S.all=false}render()}}
function render(){
 seg(document.getElementById('s-w'),'w',['1d','7d','30d'].filter(w=>w in M.windows).map(w=>[w,w.toUpperCase()]));
 seg(document.getElementById('s-c'),'c',[['all','All'],...M.cats.filter(c=>M.tokens.some(t=>t.category===c.id)).map(c=>[c.id,c.label])]);
 seg(document.getElementById('s-g'),'g',[['all','All wallets'],...M.groups.map(g=>[g.id,g.label.replace(' wallets','')])]);
 seg(document.getElementById('s-d'),'dir',[['buy','Buying'],['sell','Selling']]);
 document.getElementById('read').textContent=M.reads[S.w];
 const T=toks();document.getElementById('count').textContent=T.length+' of '+M.tokens.length+' tokens';
 const wl=M.labels[S.w];
 // ranking
 const rows=T.map(t=>({t,v:t.w[S.w][S.g].net})).sort((a,b)=>S.dir==='buy'?b.v-a.v:a.v-b.v);
 const max=Math.max(1,...rows.map(r=>Math.abs(r.v)));
 const show=S.all?rows:rows.slice(0,12);
 if(!S.sel||!T.includes(S.sel))S.sel=rows[0]&&rows[0].t;
 document.getElementById('rk-h').textContent=(S.g==='all'?'All wallets':GL[S.g])+', '+(S.dir==='buy'?'buying most':'selling most');
 document.getElementById('rk-sub').textContent=`USD bought less USD sold by ${S.g==='all'?'every wallet we count':GL[S.g].toLowerCase()+' ('+M.groups.find(g=>g.id===S.g).range+')'} in each token's main pools, ${wl}.`;
 document.getElementById('rank').innerHTML=show.map((r,i)=>{const w=r.t.w[S.w];const ld=S.g==='all'&&w.lead?GL[w.lead].replace(' wallets',''):'';
  return `<div class="rrow ${r.t===S.sel?'sel':''}" data-s="${r.t.symbol}"><span class="n">${i+1}</span><span class="tk"><b>${r.t.symbol}</b>${(r.t.w[S.w].passthrough||0)>=60?'<i class="pt" title="Mostly pass-through volume">pass-through</i>':''}<small>${ld?'Led by '+ld.toLowerCase():r.t.name}</small></span>${bar(r.v,max)}<span class="val ${cls(r.v)}">${sg(r.v)}</span></div>`}).join('');
 document.getElementById('rank').onclick=e=>{const r=e.target.closest('.rrow');if(!r)return;S.sel=M.tokens.find(t=>t.symbol===r.dataset.s);render()};
 const mb=document.getElementById('more');mb.hidden=rows.length<=12;mb.textContent=S.all?'Show top 12':'Show all '+rows.length;mb.onclick=()=>{S.all=!S.all;render()};
 // across
 const gids=['whale','big','mid','small'];
 const acc=gids.map(g=>({g,net:T.reduce((s,t)=>s+t.w[S.w][g].net,0),up:T.filter(t=>t.w[S.w][g].net>1).length,dn:T.filter(t=>t.w[S.w][g].net<-1).length}));
 const tot={net:acc.reduce((s,a)=>s+a.net,0),up:T.filter(t=>t.w[S.w].all.net>1).length,dn:T.filter(t=>t.w[S.w].all.net<-1).length};
 const fresh={net:T.reduce((s,t)=>s+t.w[S.w].all.fresh_net,0),n:T.reduce((s,t)=>s+t.w[S.w].all.fresh_n,0)};
 const bots={net:T.reduce((s,t)=>s+t.w[S.w].bots.net,0),n:T.reduce((s,t)=>s+t.w[S.w].bots.wallets,0)};
 const amax=Math.max(1,...acc.map(a=>Math.abs(a.net)),Math.abs(tot.net));
 document.getElementById('acc-sub').textContent=`${T.length} tokens, ${wl}. Bought more and sold more count tokens.`;
 document.getElementById('across').innerHTML=`<tr><th>Wallets</th><th style="width:24%"></th><th>Net</th><th>Bought more</th><th>Sold more</th></tr>`+
  `<tr class="tot"><td>All wallets</td><td>${bar(tot.net,amax)}</td><td class="${cls(tot.net)}">${sg(tot.net)}</td><td>${tot.up}</td><td>${tot.dn}</td></tr>`+
  acc.map(a=>{const g=M.groups.find(x=>x.id===a.g);return `<tr><td>${g.label}<span class="rg">${g.range}</span></td><td>${bar(a.net,amax)}</td><td class="${cls(a.net)}">${sg(a.net)}</td><td>${a.up}</td><td>${a.dn}</td></tr>`}).join('')+
  `<tr class="apart"><td>Fresh wallets<span class="rg">held none at the start</span></td><td></td><td class="${cls(fresh.net)}">${sg(fresh.net)}</td><td colspan="2">${fresh.n.toLocaleString()} wallets</td></tr>`+
  `<tr class="apart"><td>Bots and contracts<span class="rg">shown apart</span></td><td></td><td>${sg(bots.net)}</td><td colspan="2">${bots.n.toLocaleString()} addresses</td></tr>`;
 // token detail
 const t=S.sel;if(!t){document.getElementById('tdet').innerHTML='';return}
 const w=t.w[S.w];const dmax=Math.max(1,...gids.map(g=>Math.abs(w[g].net)),Math.abs(w.bots.net));
 const pc=w.price_chg==null?'':(w.price_chg>=0?'+':'−')+Math.abs(w.price_chg).toFixed(1)+'%';
 document.getElementById('tdet').innerHTML=`<div class="hd"><b>${t.symbol}</b><span>${t.name} · ${({memes:'Meme',stocks:'Stock token',agents:'AI & agents',ecosystem:'Ecosystem'})[t.category]}</span>
  <a class="chip" style="margin-left:auto" href="https://www.geckoterminal.com/robinhood/tokens/${t.token}" target="_blank" rel="noopener">Chart ↗</a></div>
  <div class="kpis"><div class="kpi"><b class="${'val '+cls(w.all.net)}" style="text-align:left">${sg(w.all.net)}</b><span>Net, all wallets</span></div>
  <div class="kpi"><b>${w.all.wallets.toLocaleString()}</b><span>Wallets traded</span></div>
  <div class="kpi"><b>${pc||'—'}</b><span>Price, ${S.w==='1d'?'day':parseInt(S.w)+' days'}</span></div></div>
  <div class="mini h"><span>Wallets</span><span></span><span>Net</span><span>Buyers</span><span>Sellers</span></div>`+
  gids.map(g=>`<div class="mini"><span>${GL[g]}</span>${bar(w[g].net,dmax)}<span class="${'val '+cls(w[g].net)}">${sg(w[g].net)}</span><span>${w[g].buyers}</span><span>${w[g].sellers}</span></div>`).join('')+
  `<div class="mini"><span style="color:var(--soft)">Fresh wallets</span><span></span><span class="${'val '+cls(w.all.fresh_net)}">${sg(w.all.fresh_net)}</span><span>${w.all.fresh_n}</span><span>—</span></div>`+
  `<div class="mini"><span style="color:var(--soft)">Bots and contracts</span>${bar(w.bots.net,dmax)}<span class="val">${sg(w.bots.net)}</span><span>${w.bots.buyers}</span><span>${w.bots.sellers}</span></div>`+
  (w.passthrough!=null&&w.passthrough>=40?`<p class="flag">${Math.round(w.passthrough)}% of this ${S.w==='1d'?"day's":S.w==='7d'?"week's":"month's"} volume came from wallets that held none before and none after. That's usually volume farming or wash trading, so read the net with care.</p>`:'')+
  `<p class="note">${w.swaps.toLocaleString()} swaps in ${t.pools} main pools. Priced from ${t.pricing}. Pass-through share: ${w.passthrough==null?'\u2014':Math.round(w.passthrough)+'%'}.${t.category==='stocks'?' Net minted on the chain: '+sg(w.minted)+'.':''}</p>`;
 // stock tokens: minted vs bought
 const st=document.getElementById('stocks');
 if(S.c==='all'||S.c==='stocks'){st.hidden=false;document.getElementById('sread').textContent=M.sreads[S.w];
  const SS=M.tokens.filter(x=>x.category==='stocks').sort((a,b)=>Math.abs(b.w[S.w].minted)+Math.abs(b.w[S.w].all.net)-Math.abs(a.w[S.w].minted)-Math.abs(a.w[S.w].all.net));
  const smax=Math.max(1,...SS.map(x=>Math.max(Math.abs(x.w[S.w].minted),Math.abs(x.w[S.w].all.net))));
  document.getElementById('stbl').innerHTML=`<tr><th>Stock token</th><th>Minted, net</th><th style="width:22%"></th><th>DEX net, all wallets</th><th style="width:22%"></th><th>Bots</th><th>Whales</th><th>Fresh wallets</th></tr>`+
   SS.map(x=>{const v=x.w[S.w];return `<tr><td><b>${x.symbol}</b> <span class="rg">${x.name}</span></td><td class="${cls(v.minted)}">${sg(v.minted)}</td><td>${bar(v.minted,smax)}</td><td class="${cls(v.all.net)}">${sg(v.all.net)}</td><td>${bar(v.all.net,smax)}</td><td style="color:var(--soft)">${sg(v.bots.net)}</td><td class="${cls(v.whale.net)}">${sg(v.whale.net)}</td><td class="${cls(v.all.fresh_net)}">${sg(v.all.fresh_net)}</td></tr>`}).join('')}
 else st.hidden=true;
}
render();
"""


def page(model):
    """The Flows content, its CSS and its data. Not a whole page: build_v4.flows_page() puts it in the site shell."""
    data = json.dumps(model, separators=(",", ":")).replace("</", "<\\/")
    ex = model["excluded"] + not_shown(model)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Flows content</title>
<!--flows-head--><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet"><!--/flows-head-->
<style id="flows-css">{CSS}</style></head><body>
<!--flows--><div class="fl">
<div class="over">Flows<span class="beta">BETA</span></div>
<h1>Who's buying Robinhood Chain</h1>
<p class="read" id="read"></p>
<p class="meta">Through {FM.dlong(model['asof'])}, end of day UTC. Read from Robinhood Chain, swap by swap, and priced with GeckoTerminal. Describes on-chain behaviour; not investment advice.</p>
<div class="ctl"><div class="seg" id="s-w"></div><div class="seg" id="s-c"></div><span class="count" id="count"></span></div>
<div class="grid">
 <section class="card"><div class="ch"><div><h2 id="rk-h"></h2><p class="sub" id="rk-sub"></p></div></div>
  <div class="ctl" style="margin:16px 0 0"><div class="seg sm" id="s-g"></div><div class="seg sm" id="s-d"></div></div>
  <div class="rank" id="rank"></div><button class="more" id="more"></button></section>
 <section class="card"><div class="ch"><div><h2>Across the chain</h2><p class="sub" id="acc-sub"></p></div></div>
  <table id="across" style="margin-top:16px"></table>
  <p class="note">Net: each group's net in every token shown, added together. All wallets is the four size groups together; fresh wallets and bots are counted apart from it.</p>
  <div class="tdet" id="tdet"></div></section>
</div>
<section class="sec" id="stocks"><div class="ch"><div><h2>Stock tokens: minted vs bought</h2>
 <p>Minted is the change in each stock token's supply on the chain. DEX net is what wallets bought less what they sold in the pools; bots are the high-frequency traders and arbitrage contracts, counted apart.</p></div></div>
 <p class="sread" id="sread"></p><div class="card" style="padding:8px 24px 4px;overflow-x:auto"><table id="stbl" style="min-width:860px"></table></div></section>
<details class="how"><summary>How to read it</summary><dl>
<dt>Net</dt><dd>USD bought less USD sold in each token's main pools (pools with $5K+ of liquidity on Uniswap v3 and v4, Aerodrome Slipstream, Ramses and other v2/v3-style DEXs). Every swap is read from Robinhood Chain and valued at that hour's price.</dd>
<dt>Who traded</dt><dd>The wallet whose token balance changed in the swap. Routers and aggregators pass tokens through and net to zero, so the trade lands on the wallet behind it.</dd>
<dt>Wallet size</dt><dd>Each wallet that traded is sorted by its holding at the window's start or end, whichever is larger: whales $50K+, big $5K to $50K, mid $500 to $5K, small under $500. A whale that sold everything still counts as a whale.</dd>
<dt>Fresh wallets</dt><dd>Wallets that held none of the token at the start of the window, ended up net buyers, and still hold at least half of what they bought. It's the closest on-chain read of new money that stayed.</dd>
<dt>Pass-through</dt><dd>The share of a token's volume from wallets that held none of it before and none after: they bought and moved it on, or received and sold it. A high share usually means volume farming or wash trading.</dd>
<dt>Bots and contracts</dt><dd>Wallets trading more than 100 times a day, and arbitrage, market-making and vault contracts trading 10+ times a day. They're shown apart and never inside a size group. Smart wallets (most RH traders use EIP-7702 or small wallet proxies) count as wallets.</dd>
<dt>Checks</dt><dd>What wallets traded matches GeckoTerminal's volume for the same pools within about 7% for memes (CASHCAT +6%, AI −7% on Nov 20). Stock tokens read lower (SPCX −14%) because arbitrage that buys in one pool and sells in another inside a single transaction nets to zero for the wallet, while GeckoTerminal counts both legs.</dd>
<dt>Windows</dt><dd>{windows_note(model)}</dd>
<dt>Not counted</dt><dd>Plain transfers between wallets, adding or removing liquidity, and pools under $5K. Stock tokens' minting is shown separately, never as buying.</dd>
<dt>Tokens</dt><dd>{len(model['tokens'])} tokens: memes and AI tokens with $100K+ of liquidity and 14+ days of trading, the largest stock tokens, and ecosystem tokens. {ex}</dd>
</dl></details>
<p class="fsrc">Data: Robinhood Chain (swaps, transfers, balances) and GeckoTerminal (prices, pools), through {FM.dlong(model['asof'])} UTC.</p>
</div>
<script type="application/json" id="flows-data">{data}</script>
<script>{JS}</script><!--/flows-->
</body></html>"""


def model_from_page(path):
    """The model a built page carries, e.g. to read its date (model_from_page(path)["asof"])."""
    s = open(path).read()
    a = s.index('<script type="application/json" id="flows-data">') + len('<script type="application/json" id="flows-data">')
    return json.loads(s[a:s.index("</script>", a)])


def main():
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    if len(pos) < 2:
        raise SystemExit(__doc__)
    cache_dir, out = pos[0], pos[1]
    try:
        model = FM.build(cache_dir)
        wids = list(model["windows"])
        model["reads"] = {w: read(model, w) for w in wids}
        model["sreads"] = {w: stock_read(model, w) for w in wids}
        model["labels"] = {w: window_label(model, w) for w in wids}
        model["excluded"] = pos[2] if len(pos) > 2 else opt.get("note", NOTE)
        errs, warns = check(model)
    except (Exception, SystemExit) as e:
        model, errs, warns = None, [f"could not build the model: {e}"], []
    for w in warns:
        print(f"::warning title=Flows::{w}")
    if errs:
        for e in errs:
            print(f"::error title=Flows::{e}")
        if os.path.exists(out):
            print(f"::error title=Flows::page not updated; the last good page stays up (through {model_from_page(out)['asof']})")
            return 0
        print("::error title=Flows::no earlier page to fall back on")
        return 1
    tmp = out + ".tmp"
    open(tmp, "w").write(page(model))
    os.replace(tmp, out)
    if opt.get("model"):
        json.dump(model, open(opt["model"], "w"), separators=(",", ":"))
    print(f"flows page: {len(model['tokens'])} tokens through {model['asof']}, windows {', '.join(w.upper() for w in model['windows'])}")
    for w in model["windows"]:
        print(f"  {w.upper()} | {model['reads'][w]}\n      | {model['sreads'][w]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
