"""Render the Smart money page from the flows cache. Same look as Flows: dark, lime accents, card layout.
Usage: build_smart_page.py <cache_dir> <out.html>"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows_model as FM
import smart_money as SM
import build_flows_page as P



def read(m):
    T = m["tokens"]
    if not m["n_smart"]:
        return (f"No wallet made $500+ trading RH tokens from {FM.dshort(m['sel']['from'])} to {FM.dshort(m['sel']['to'])} "
                f"under our rules, so there's no smart money list for {FM.dshort(m['day'])}.")
    sm = sum(t["smart"]["net"] for t in T); cr = sum(t["crowd"]["net"] for t in T)
    sel = f"{FM.dshort(m['sel']['from'])} to {FM.dshort(m['sel']['to'])}"
    verb = "bought" if sm >= 0 else "sold"
    lead = sorted([t for t in T if (t["smart"]["net"] > 0) == (sm >= 0) and abs(t["smart"]["net"]) >= 1000],
                  key=lambda t: -abs(t["smart"]["net"]))[:2]
    s = (f"The {m['n_smart']} most profitable wallets of {sel} net {verb} {P.fm(sm)} of Robinhood Chain tokens on "
         f"{FM.dshort(m['day'])}")
    if lead:
        s += ", mostly " + " and ".join(f"{t['symbol']} ({P.signed(t['smart']['net'])})" for t in lead)
    s += f", while everyone else net {'bought' if cr >= 0 else 'sold'} {P.fm(cr)}."
    # where they went against the crowd, by the size of their own move
    dis = sorted([t for t in T if t["smart"]["net"] * t["crowd"]["net"] < 0 and abs(t["smart"]["net"]) >= 2000
                  and t["smart"]["buyers"] + t["smart"]["sellers"] >= 3], key=lambda t: -abs(t["smart"]["net"]))
    buys = [t["symbol"] for t in dis if t["smart"]["net"] > 0][:2]
    sells = [t["symbol"] for t in dis if t["smart"]["net"] < 0][:2]
    if buys:
        s += f" They bought {' and '.join(buys)} as everyone else sold."
    elif sells:
        s += f" They sold {' and '.join(sells)} as everyone else bought."
    return s


JS = r"""
const M=JSON.parse(document.getElementById('sm-data').textContent);
const S={c:'all',dir:'buy',all:false};
const fm=x=>{const a=Math.abs(x);return a>=1e9?'$'+(a/1e9).toFixed(2)+'B':a>=1e6?'$'+(a/1e6).toFixed(1)+'M':a>=1e4?'$'+Math.round(a/1e3)+'K':a>=1e3?'$'+(a/1e3).toFixed(1)+'K':'$'+Math.round(a).toLocaleString()};
const sg=x=>Math.abs(x)<0.5?'$0':(x>=0?'+':'−')+fm(x);
const cls=x=>x>=0?'p':'m';
const short=a=>a.slice(0,6)+'…'+a.slice(-4);
function bar(v,max){const w=max?Math.min(50,Math.abs(v)/max*50):0;return `<div class="bar"><span class="${v>=0?'p':'m'}" style="width:${w.toFixed(2)}%"></span></div>`}
function seg(el,key,opts){el.innerHTML=opts.map(([v,l])=>`<button data-v="${v}" class="${S[key]===v?'on':''}">${l}</button>`).join('');
 el.onclick=e=>{const b=e.target.closest('button');if(!b)return;S[key]=b.dataset.v;if(key==='c')S.all=false;render()}}
function render(){
 seg(document.getElementById('s-c'),'c',[['all','All'],...M.cats.filter(c=>M.tokens.some(t=>t.category===c.id)).map(c=>[c.id,c.label])]);
 seg(document.getElementById('s-d'),'dir',[['buy','Buying'],['sell','Selling']]);
 const T=M.tokens.filter(t=>(S.c==='all'||t.category===S.c)&&t.smart.buyers+t.smart.sellers>0);
 document.getElementById('count').textContent=T.length+' tokens they traded on '+M.dayl;
 const rows=T.filter(t=>S.dir==='buy'?t.smart.net>0:t.smart.net<0).sort((a,b)=>S.dir==='buy'?b.smart.net-a.smart.net:a.smart.net-b.smart.net);
 const max=Math.max(1,...rows.map(t=>Math.abs(t.smart.net)));
 const show=S.all?rows:rows.slice(0,10);
 document.getElementById('rk-h').textContent='Smart money, '+(S.dir==='buy'?'buying most':'selling most');
 document.getElementById('rank').innerHTML=show.length?show.map((t,i)=>`<div class="rrow"><span class="n">${i+1}</span><span class="tk"><b>${t.symbol}</b><small>${t.smart.buyers} bought, ${t.smart.sellers} sold</small></span>${bar(t.smart.net,max)}<span class="val ${cls(t.smart.net)}">${sg(t.smart.net)}</span></div>`).join(''):`<p class="note">None of them ${S.dir==='buy'?'bought':'sold'} anything here on ${M.dayl}.</p>`;
 const mb=document.getElementById('more');mb.hidden=rows.length<=10;mb.textContent=S.all?'Show top 10':'Show all '+rows.length;mb.onclick=()=>{S.all=!S.all;render()};
 // where they disagree
 const dis=T.filter(t=>t.smart.net*t.crowd.net<0).sort((a,b)=>Math.abs(b.smart.net)-Math.abs(a.smart.net)).slice(0,8);
 document.getElementById('dis').innerHTML=dis.length?`<tr><th>Token</th><th>Smart money</th><th>Everyone else</th><th>Price, day</th></tr>`+
  dis.map(t=>`<tr><td><b>${t.symbol}</b><span class="rg">${t.smart.net>0?'they bought, the rest sold':'they sold, the rest bought'}</span></td><td class="${cls(t.smart.net)}">${sg(t.smart.net)}</td><td class="${cls(t.crowd.net)}">${sg(t.crowd.net)}</td><td style="color:var(--soft)">${t.chg==null?'—':(t.chg>=0?'+':'−')+Math.abs(t.chg).toFixed(1)+'%'}</td></tr>`).join('')
  :`<tr><td style="border:0;color:var(--soft)">No splits in this category on ${M.dayl}: where they traded, everyone else went the same way.</td></tr>`;
}
render();
"""

EXTRA_CSS = r"""
.fl .kpis.top3{grid-template-columns:repeat(3,1fr);margin:28px 0 0;max-width:980px}
.fl .kpis.top3 .kpi b{font-size:22px}
.fl .addr{font:13px var(--mono);color:var(--ink)}
.fl #lb td,.fl #lb th{padding-left:14px}
.fl #lb th:first-child,.fl #lb td:first-child{padding-left:0;width:28px;color:var(--faint);font:12px var(--mono)}
.fl #lb td:nth-child(2),.fl #lb th:nth-child(2){text-align:left}
.fl #dis td .rg{display:block}
.fl #dis th{padding-left:14px;white-space:nowrap}
@media (max-width:620px){.fl .kpis.top3{grid-template-columns:1fr}
.fl #dis th{white-space:normal;padding-left:8px}
.fl #dis th:nth-child(4),.fl #dis td:nth-child(4){display:none}}
"""


def page(m):
    data = json.dumps(m, separators=(",", ":")).replace("</", "<\\/")
    sel = f"{FM.dshort(m['sel']['from'])} to {FM.dshort(m['sel']['to'])}"
    day = FM.dshort(m["day"])
    T = m["tokens"]
    sm = sum(t["smart"]["net"] for t in T); cr = sum(t["crowd"]["net"] for t in T)
    edge = m["total_res"] / m["total_vol"] * 100 if m["total_vol"] else 0
    lb = "".join(
        f"<tr><td>{i + 1}</td><td><span class=\"addr\" title=\"{w['a']}\">{w['a'][:6]}…{w['a'][-4:]}</span></td>"
        f"<td class=\"p\">{P.signed(w['res'])}</td><td>{P.fm(w['vol'])}</td><td>{w['res'] / w['vol'] * 100:.0f}%</td>"
        f"<td>{w['days']}</td><td>{w['best'] or '—'} <span class=\"rg\" style=\"display:inline\">{P.signed(w['best_res']) if w['best'] else ''}</span></td>"
        + (f"<td class=\"{'p' if w['day_net'] >= 0 else 'm'}\">{P.signed(w['day_net'])} <span class=\"rg\" style=\"display:inline\">{w['day_top']}</span></td>"
           if w["day_top"] else "<td style=\"color:var(--faint)\">didn't trade</td>") + "</tr>"
        for i, w in enumerate(m["board"]))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Smart money content</title>
<!--flows-head--><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet"><!--/flows-head-->
<style id="flows-css">{P.CSS}{EXTRA_CSS}</style></head><body>
<!--flows--><div class="fl">
<div class="over">Smart money<span class="beta">BETA</span></div>
<h1>What RH's best traders are buying</h1>
<p class="read">{read(m)}</p>
<p class="meta">Smart money is the {m['n_smart']} wallets with the best trading results from {sel}. Their moves are from {FM.dlong(m['day'])}, end of day UTC, a day they weren't picked on. Read from Robinhood Chain, swap by swap. Describes on-chain behaviour; not investment advice.</p>
<div class="kpis top3">
 <div class="kpi"><b class="val p" style="text-align:left">{P.signed(m['total_res'])}</b><span>What they made, {sel}, on {P.fm(m['total_vol'])} traded ({edge:.0f}%)</span></div>
 <div class="kpi"><b>{m['active']} of {m['n_smart']}</b><span>Traded on {day}</span></div>
 <div class="kpi"><b class="val {'p' if sm >= 0 else 'm'}" style="text-align:left">{P.signed(sm)}</b><span>Their net on {day}. Everyone else: {P.signed(cr)}</span></div>
</div>
<div class="ctl"><div class="seg" id="s-c"></div><span class="count" id="count"></span></div>
<div class="grid">
 <section class="card"><div class="ch"><div><h2 id="rk-h"></h2><p class="sub">USD bought less USD sold by the smart money wallets in each token's main pools on {day}.</p></div></div>
  <div class="ctl" style="margin:16px 0 0"><div class="seg sm" id="s-d"></div></div>
  <div class="rank" id="rank"></div><button class="more" id="more"></button></section>
 <section class="card"><div class="ch"><div><h2>Where they split from everyone else</h2><p class="sub">Tokens where smart money and every other wallet went opposite ways on {day}, biggest smart-money move first.</p></div></div>
  <table id="dis" style="margin-top:16px"></table>
  <p class="note">Everyone else: every other wallet that traded, minus bots and contracts.</p></section>
</div>
<section class="sec"><div class="ch"><div><h2>The 25 most profitable wallets, {sel}</h2>
 <p>Ranked by trading result across the {len(T)} tokens we track. Edge is the result per dollar traded. The last column is what each one did on {day}.</p></div></div>
 <div class="card" style="padding:8px 24px 4px;overflow-x:auto;margin-top:18px"><table id="lb" style="min-width:820px">
 <tr><th>#</th><th>Wallet</th><th>Result</th><th>Traded</th><th>Edge</th><th>Days</th><th>Best token</th><th>On {day}</th></tr>{lb}</table></div></section>
<details class="how"><summary>How to read it</summary><dl>
<dt>Smart money</dt><dd>The {m['n_smart']} wallets with the best trading result from {sel}, among wallets that traded $5K+ with 3+ trades on 2+ days and made at least $500. Bots (more than 100 trades a day in a token) and contracts other than smart wallets are left out. The list is picked again every day.</dd>
<dt>Result</dt><dd>For each token: USD sold less USD bought, plus the tokens the wallet's trades added or removed, valued at the token's close on {FM.dshort(m['sel']['to'])}. A wallet that bought below that close or sold above it made money; tokens received or sent by plain transfers never count as profit.</dd>
<dt>No hindsight</dt><dd>The wallets are picked on the days before {day} and their moves are shown from {day}, so what they bought on {day} isn't why they were picked.</dd>
<dt>Edge</dt><dd>Result divided by USD traded. 10% means they made $10 for every $100 they traded.</dd>
<dt>Limits</dt><dd>The picking window is short while history fills in ({sel}); it grows to 29 days. On days a wallet also moved tokens by plain transfer, its trades are valued at that day's average price, so same-day round trips count less precisely.</dd>
</dl></details>
<p class="fsrc">Data: Robinhood Chain (swaps, transfers, balances) and GeckoTerminal (prices, pools), through {FM.dlong(m['asof'])} UTC.</p>
</div>
<script type="application/json" id="sm-data">{data}</script>
<script>{JS}</script><!--/flows-->
</body></html>"""


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    m = SM.build(sys.argv[1])
    m["dayl"] = FM.dshort(m["day"])
    html = page(m)
    tmp = sys.argv[2] + ".tmp"
    open(tmp, "w").write(html)
    os.replace(tmp, sys.argv[2])
    print(read(m))


if __name__ == "__main__":
    main()
