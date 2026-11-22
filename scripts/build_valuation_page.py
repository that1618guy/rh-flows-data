"""Render the revenue multiple view: what the market pays for each RH-native project's revenue.
Usage: build_valuation_page.py <valuation.json> <out.html> <asof YYYY-MM-DD>"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows_model as FM
import build_flows_page as P

MIN_LIQ = 50_000
MIN_REV30 = 10_000
YEAR_7 = 365 / 7


def prep(v):
    rows = [r for r in v["rows"] if r["liq"] >= MIN_LIQ and r["rev30"] >= MIN_REV30]
    for r in rows:
        r["multiple7"] = r["fdv"] / (r["rev7"] * YEAR_7) if r["rev7"] > 0 else None
        r["flags"] = [f for f in r["flags"] if f != "thin liquidity"]
    return rows


def read(rows):
    N = len(rows)
    if not rows:
        return "No RH-native project with a token cleared $50K of liquidity and $10K of revenue in the last 30 days."
    names = lambda xs: ", ".join(xs[:-1]) + (" and " if len(xs) > 1 else "") + (xs[-1] if xs else "")
    big = max(rows, key=lambda r: r["fees30"])
    under1 = [r["name"] for r in rows if r["multiple"] < 1]
    slow = sum(1 for r in rows if r["pace"] is not None and r["pace"] < 0)
    s = f"{big['name']}, the biggest by fees, trades at {big['multiple']:.1f}× a year of revenue"
    s += f" and paid holders {big['holder_yield']:.0f}% a year at today's price. " if big["holder_yield"] else ". "
    if under1:
        s += f"{names(under1)} trade below 1×, which usually means the revenue doesn't fully reach the token. "
    else:
        c = rows[0]
        s += f"{c['name']} is the cheapest at {c['multiple']:.1f}×. "
    s += f"Revenue is cooling: {slow} of {N} earned less last week than their 30-day average."
    return s


JS = r"""
const M=JSON.parse(document.getElementById('val-data').textContent);
const S={sort:'multiple'};
const fm=x=>{const a=Math.abs(x);return a>=1e9?'$'+(a/1e9).toFixed(2)+'B':a>=1e6?'$'+(a/1e6).toFixed(1)+'M':a>=1e4?'$'+Math.round(a/1e3)+'K':a>=1e3?'$'+(a/1e3).toFixed(1)+'K':'$'+Math.round(a).toLocaleString()};
const mx=x=>x==null?'—':x<10?x.toFixed(1)+'×':Math.round(x)+'×';
const pct=x=>x==null?'—':(x>=0?'+':'−')+Math.abs(x).toFixed(0)+'%';
const FL={'check':'Below 1×: check how revenue reaches the token','small float':'Market cap is under a quarter of fully diluted value','double counted':'DefiLlama also counts these fees under another project'};
function seg(el,key,opts){el.innerHTML=opts.map(([v,l])=>`<button data-v="${v}" class="${S[key]===v?'on':''}">${l}</button>`).join('');
 el.onclick=e=>{const b=e.target.closest('button');if(!b)return;S[key]=b.dataset.v;render()}}
function render(){
 seg(document.getElementById('s-s'),'sort',[['multiple','Cheapest first'],['yield','Holder yield'],['rev','Biggest revenue']]);
 const R=[...M.rows].sort((a,b)=>S.sort==='multiple'?a.multiple-b.multiple:S.sort==='yield'?(b.holder_yield||-1)-(a.holder_yield||-1):b.rev30-a.rev30);
 document.getElementById('vt').innerHTML=`<tr><th>Project</th><th>Fully diluted</th><th>Liquidity</th><th>Revenue, 30d</th><th>Multiple</th><th>At last week's pace</th><th>Holder yield</th><th>Fees vs prior 30d</th></tr>`+
  R.map(r=>`<tr><td><a href="${r.llama}" target="_blank" rel="noopener"><b>${r.name}</b></a>${r.flags.map(f=>`<i class="pt" title="${FL[f]||f}">${f}</i>`).join('')}<span class="rg">${r.symbol} · ${r.category}</span></td>
  <td>${fm(r.fdv)}</td><td>${fm(r.liq)}</td><td>${fm(r.rev30)}</td><td><b>${mx(r.multiple)}</b></td><td style="color:var(--soft)">${mx(r.multiple7)}</td>
  <td class="${r.holder_yield?'p':''}">${r.holder_yield?Math.round(r.holder_yield)+'%':'—'}</td><td class="${r.fees_chg==null?'':r.fees_chg>=0?'p':'m'}">${pct(r.fees_chg)}</td></tr>`).join('');
}
render();
"""

EXTRA_CSS = r"""
.fl h2.vh{font-size:46px;line-height:1.05;letter-spacing:-.035em;margin:14px 0 18px;font-weight:700}
@media (max-width:620px){.fl h2.vh{font-size:34px}}
.fl #vt th{padding-left:14px;white-space:nowrap}
.fl #vt td{padding-left:14px}
.fl #vt td:first-child{padding-left:0;white-space:normal}
.fl #vt td .rg{display:block}
.fl #vt td a{color:var(--ink);text-decoration:none}
.fl #vt td a:hover b{text-decoration:underline}
"""


def page(rows, asof):
    data = json.dumps({"rows": rows}, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Revenue multiple content</title>
<!--flows-head--><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet"><!--/flows-head-->
<style id="flows-css">{P.CSS}{EXTRA_CSS}</style></head><body>
<!--flows--><div class="fl" id="revenue-multiple">
<div class="over">Projects · revenue multiple<span class="beta">BETA</span></div>
<h2 class="vh">What you pay for RH revenue</h2>
<p class="read">{read(rows)}</p>
<p class="meta">{len(rows)} RH-native projects with a token, $50K+ of liquidity and $10K+ of revenue in 30 days. Revenue from DefiLlama through {FM.dlong(asof)}; prices from GeckoTerminal. Not investment advice.</p>
<div class="ctl"><div class="seg" id="s-s"></div><span class="count">{len(rows)} projects</span></div>
<div class="card" style="padding:8px 24px 4px;overflow-x:auto"><table id="vt" style="min-width:980px"></table></div>
<details class="how"><summary>How to read it</summary><dl>
<dt>Multiple</dt><dd>Fully diluted value divided by a year of revenue at the last 30 days' rate. 5× means the market values the project at five years of its current revenue. Lower is cheaper.</dd>
<dt>At last week's pace</dt><dd>The same multiple using only the last 7 days of revenue. When it's higher than the multiple, revenue is slowing.</dd>
<dt>Holder yield</dt><dd>What DefiLlama counts as holder revenue (buybacks, burns and payouts to stakers or holders) over the last 30 days, at a yearly rate, divided by fully diluted value. It's what holders got back, not a promise of what they'll get.</dd>
<dt>Revenue</dt><dd>The part of fees the project keeps, as DefiLlama defines it for each project (open the project on DefiLlama for its method). Only revenue earned on Robinhood Chain counts.</dd>
<dt>Flags</dt><dd>Check: below 1×, which usually means the revenue doesn't fully reach the token or the market doubts it will last; read the project's method before calling it cheap. Small float: market cap is under a quarter of fully diluted value, so most of the supply isn't trading yet. Double counted: DefiLlama also counts these fees under another project.</dd>
<dt>Included</dt><dd>RH-native projects with a token on Robinhood Chain, $50K+ of liquidity and $10K+ of revenue in the last 30 days. Multichain apps are left out.</dd>
</dl></details>
<p class="fsrc">Data: DefiLlama (fees, revenue, holder revenue) and GeckoTerminal (prices, valuations, liquidity).</p>
</div>
<script type="application/json" id="val-data">{data}</script>
<script>(()=>{{{JS}}})();</script><!--/flows-->
</body></html>"""


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    rows = prep(json.load(open(sys.argv[1])))
    html = page(rows, sys.argv[3])
    tmp = sys.argv[2] + ".tmp"
    open(tmp, "w").write(html)
    os.replace(tmp, sys.argv[2])
    print(read(rows))
    for r in rows:
        print(f"  {r['name'][:22]:22s} x{r['multiple']:.2f} (7d x{(r['multiple7'] or 0):.2f}) yield {r['holder_yield']} {r['flags']}")


if __name__ == "__main__":
    main()
