"""The revenue multiple section of the Projects page: what the market pays for each RH-native project's revenue.

Built from revenue_multiple.py's JSON. `view()` gives the parts to place on the Projects page, below the projects
table: body (one <section id="val-sec">), css, js and the data block (id="val-data"). Run directly, it writes
the section as a page of its own, for checking.

Usage: build_valuation_page.py <valuation.json> <out.html> <asof YYYY-MM-DD>
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import site_shell as SH
from site_shell import dshort

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
    """At most three sentences."""
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
        c = min(rows, key=lambda r: r["multiple"])
        s += f"{c['name']} is the cheapest at {c['multiple']:.1f}×. "
    s += f"Revenue is cooling: {slow} of {N} earned less last week than their 30-day average." if slow * 2 >= N else \
         f"{N - slow} of {N} earned more last week than their 30-day average."
    return s


def view(rows, asof):
    keep = ("name", "symbol", "category", "llama", "fdv", "liq", "rev30", "multiple", "multiple7", "holder_yield", "fees_chg", "flags")
    data = {"rows": [{k: r.get(k) for k in keep} for r in rows]}
    body = f"""
<section class="sec" id="val-sec">
<div class="sec-h"><div><h2>What you pay for RH revenue</h2><p>{read(rows)}</p></div>
<div class="seg" id="v-sort" role="group" aria-label="Order"></div></div>
<div class="panel scroll"><table class="tbl" id="v-tbl"></table></div>
<p class="note" style="margin-top:12px">{len(rows)} RH-native projects with a token, $50K+ of liquidity and $10K+ of revenue in 30 days. Revenue from DefiLlama through {dshort(asof)}; prices from GeckoTerminal.</p>
<details class="how"><summary>How to read it</summary><dl>
<dt>Multiple</dt><dd>Fully diluted value divided by a year of revenue at the last 30 days' rate. 5× means the market values the project at five years of its current revenue. Lower is cheaper.</dd>
<dt>At last week's pace</dt><dd>The same multiple using only the last 7 days of revenue. When it's higher than the multiple, revenue is slowing.</dd>
<dt>Holder yield</dt><dd>What DefiLlama counts as holder revenue (buybacks, burns and payouts to stakers or holders) over the last 30 days, at a yearly rate, divided by fully diluted value. It's what holders got back, not a promise of what they'll get.</dd>
<dt>Revenue</dt><dd>The part of fees the project keeps, as DefiLlama defines it for each project. Only revenue earned on Robinhood Chain counts.</dd>
<dt>Flags</dt><dd>Check: below 1×, which usually means the revenue doesn't fully reach the token or the market doubts it will last. Small float: market cap is under a quarter of fully diluted value. Double counted: DefiLlama also counts these fees under another project.</dd>
<dt>Included</dt><dd>RH-native projects with a token on Robinhood Chain, $50K+ of liquidity and $10K+ of revenue in the last 30 days. Multichain apps are left out.</dd>
</dl></details>
</section>
"""
    return {"id": "val", "title": "Revenue multiple", "desc": "What the market pays for each Robinhood Chain project's revenue.",
            "body": body, "css": CSS, "js": JS, "data": data}


CSS = r"""
#v-tbl{min-width:900px}
#v-tbl td:first-child a{color:var(--text);text-decoration:none}
#v-tbl td:first-child a:hover b{text-decoration:underline}
#v-tbl td:first-child b{font:600 15.5px var(--tight)}
#v-tbl td:first-child small{display:block;color:var(--soft2);font-size:12.5px}
#v-tbl .tag{margin-left:8px}
#val-sec .panel{padding:18px 22px 6px}
#val-sec .sec-h p{max-width:76ch}
"""

JS = r"""
(function(){
 const root=document.getElementById('val-sec');if(!root)return;
 const M=JSON.parse(document.getElementById('val-data').textContent),{fm,pct,esc}=SOB;
 const S={sort:'multiple'};
 const mx=x=>x==null?'—':x<10?x.toFixed(1)+'×':Math.round(x)+'×';
 const FL={'check':'Below 1×: check how revenue reaches the token','small float':'Market cap is under a quarter of fully diluted value','double counted':'DefiLlama also counts these fees under another project'};
 function render(){
  SOB.seg(root.querySelector('#v-sort'),[['multiple','Cheapest first'],['yield','Holder yield'],['rev','Biggest revenue']],S.sort,v=>{S.sort=v;render()});
  const R=[...M.rows].sort((a,b)=>S.sort==='multiple'?a.multiple-b.multiple:S.sort==='yield'?(b.holder_yield||-1)-(a.holder_yield||-1):b.rev30-a.rev30);
  root.querySelector('#v-tbl').innerHTML=`<tr><th>Project</th><th>Fully diluted</th><th>Liquidity</th><th>Revenue, 30d</th><th>Multiple</th><th>At last week's pace</th><th>Holder yield</th><th>Fees vs prior 30d</th></tr>`+
   R.map(r=>`<tr><td><a href="${esc(r.llama)}" target="_blank" rel="noopener"><b>${esc(r.name)}</b></a>${r.flags.map(f=>`<span class="tag" title="${esc(FL[f]||f)}">${esc(f)}</span>`).join('')}<small>${esc(r.symbol)} · ${esc(r.category||'')}</small></td>
   <td>${fm(r.fdv)}</td><td>${fm(r.liq)}</td><td>${fm(r.rev30)}</td><td><b>${mx(r.multiple)}</b></td><td class="mut">${mx(r.multiple7)}</td>
   <td>${r.holder_yield?Math.round(r.holder_yield)+'%':'—'}</td><td>${pct(r.fees_chg,0)}</td></tr>`).join('');
 }
 render();
})();
"""


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    rows = prep(json.load(open(sys.argv[1])))
    v = view(rows, sys.argv[3])
    v["body"] = '<section class="pg-head"><p class="kicker">Projects</p><h1>Projects</h1></section>' + v["body"]
    page = SH.page(v, f"Data through {dshort(sys.argv[3])}")
    tmp = sys.argv[2] + ".tmp"
    open(tmp, "w", encoding="utf-8").write(page); os.replace(tmp, sys.argv[2])
    print(read(rows))
    for r in rows:
        print(f"  {r['name'][:22]:22s} x{r['multiple']:.2f} (7d x{(r['multiple7'] or 0):.2f}) yield {r['holder_yield']} {r['flags']}")


if __name__ == "__main__":
    main()
