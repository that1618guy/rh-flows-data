"""The shared page shell for State of Robinhood Chain: one header, one Tools menu, one set of styles and helpers.

Every page is built as a view: {"id", "title", "body", "css", "js", "data"}. `page()` wraps one view as a full
HTML page for the live site; `bundle()` puts several views in one file, switched by the link's #name, for previews.
"""
import html, json

SITE = "State of Robinhood Chain"
# The whole site map. Overview is the home page; everything else lives in the Tools menu.
TOOLS = [
    ("Tokens", [
        ("smart-filter", "Smart Filter", "Tokens to watch, and why"),
        ("flows", "Flows", "Who is buying and selling"),
        ("smart-money", "Smart money", "What the best wallets hold and trade"),
        ("holders", "Holders", "How firmly each token is held"),
    ]),
    ("Ecosystem", [
        ("projects", "Projects", "Revenue and what the market pays"),
        ("stock-tokens", "Economies", "Stock tokens, agents, launchpads, DEXs and hooks"),
        ("map", "Map", "Every project, by what it does"),
    ]),
]
TOOL_NAMES = {i: n for _, items in TOOLS for i, n, _ in items}

MARK = ('<span class="mark" aria-hidden="true"><svg viewBox="0 0 96 96"><g transform="translate(13 13) scale(0.73)"><path fill="currentColor" '
        'd="M80.4 43.8 L64.9 37.0 L50.0 42.9 Q50.6 49.6 45.5 59.7 L15.7 72.9 L15.6 84.4 L33.2 91.9 L80.0 71.3 Z M80.3 11.2 L62.7 4.0 L15.7 22.8 '
        'L15.6 48.9 L31.9 56.4 L43.8 51.8 Q43.4 45.4 47.4 36.8 L52.6 33.2 L80.2 22.3 Z"/></g></svg></span>')
# The small moving symbols beside a state word: rising, falling, level.
SYM = {
    "up": '<svg class="sym sym-rise" viewBox="0 0 20 20" aria-hidden="true"><g class="arw" fill="none" stroke="var(--lime-ink)" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M10 16.5v-11"/><path d="M5.5 9.5 10 5l4.5 4.5"/></g></svg>',
    "down": '<svg class="sym sym-cool" viewBox="0 0 20 20" aria-hidden="true"><g class="arw" fill="none" stroke="var(--text)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.5v11"/><path d="M5.5 10.5 10 15l4.5-4.5"/></g></svg>',
    "flat": '<svg class="sym sym-flat" viewBox="0 0 20 20" aria-hidden="true"><path class="ln" d="M4 10h12" stroke="var(--text)" stroke-width="1.9" stroke-linecap="round"/></svg>',
}

CSS = r"""
/* The site's own tokens and type (Inter, Inter Tight for headings and figures, mono caps for kickers), unchanged.
   layout: one 1320px column, 68px header; hairlines and space do the grouping. */
:root{--bg:#171715;--panel:#1d1d1b;--text:#ecebe6;--soft:#a3a29b;--soft2:#6f6e68;--line:#2f2f2c;--on:#8fcf9f;--mid:#e2b56f;--off:#ef8f84;
  --lime:#ccff00;--lime-ink:#ccff00;--lime-soft:rgba(204,255,0,.07);--on-lime:#151514;
  --raised:#262624;--hover:#222220;--line-2:#3e3e3a;
  --surface:var(--panel);--ink:var(--text);--ink-2:var(--soft);--ink-3:var(--soft2);
  --up:var(--lime-ink);--down:var(--off);--up-ink:var(--lime-ink);--down-ink:var(--off);
  --font:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--tight:"Inter Tight",Inter,system-ui,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--r:12px;color-scheme:dark}
:root.light{--bg:#f8f8f6;--panel:#f0efeb;--text:#1b1a17;--soft:#605d57;--soft2:#a29f97;--line:#e5e3dd;--on:#2b7a43;--mid:#8e600c;--off:#b23f2e;
  --lime:#c6f500;--lime-ink:#4a7a00;--lime-soft:rgba(170,220,0,.16);--raised:#e6e5e0;--hover:#eae9e4;--line-2:#d2cfc7;
  --up:#467a00;--down:#f08573;--up-ink:#3f7000;--down-ink:#b23f2e;color-scheme:light}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 var(--font);-webkit-font-smoothing:antialiased}
a{color:inherit}
button{font:inherit;color:inherit}
h1,h2,h3,h4,p,ol,ul,dl,dd{margin:0}
:focus-visible{outline:2px solid var(--lime-ink);outline-offset:2px;border-radius:6px}
.num{font-variant-numeric:tabular-nums}
.wrap{max-width:1320px;margin:0 auto;padding-left:max(40px,env(safe-area-inset-left,0px));padding-right:max(40px,env(safe-area-inset-right,0px))}

/* header */
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:40;background:var(--bg)}
.hd{position:sticky;top:env(safe-area-inset-top,0px);z-index:30;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.hd .wrap{display:flex;align-items:center;gap:36px;height:68px}
.brand{display:flex;align-items:center;gap:12px;text-decoration:none;font:600 16px var(--tight);letter-spacing:-.01em;white-space:nowrap}
.mark{width:32px;height:32px;border-radius:7px;background:var(--text);color:var(--bg);display:grid;place-items:center;flex:none}
.mark svg{width:28px;height:28px}
.brand u{align-self:stretch;width:1px;margin:5px 2px;background:var(--line-2);text-decoration:none}
.brand .rh{color:var(--soft)}
.nav{display:flex;align-items:center;gap:6px}
.nav a,.nav button{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 12px;border:0;border-radius:8px;background:none;color:var(--soft);text-decoration:none;font-size:14px;cursor:pointer}
.nav a:hover,.nav button:hover{color:var(--text)}
.nav .on{color:var(--text)}
.nav button svg{transition:transform .18s ease}
.nav button[aria-expanded="true"]{color:var(--text);background:var(--raised)}
.nav button[aria-expanded="true"] svg{transform:rotate(180deg)}
.hd-r{margin-left:auto;display:flex;align-items:center;gap:14px}
.stamp{color:var(--soft);font-size:13px;white-space:nowrap}
.theme{width:34px;height:34px;border-radius:8px;border:1px solid var(--line);background:none;color:var(--text);display:grid;place-items:center;cursor:pointer}
.theme svg{width:16px;height:16px}
.menu{position:absolute;left:0;right:0;top:100%;pointer-events:none}
.menu .wrap{position:relative}
.sheet{position:absolute;top:8px;left:calc(max(40px,env(safe-area-inset-left,0px)) + 250px);width:min(600px,calc(100vw - 40px));display:grid;grid-template-columns:1fr 1fr;gap:8px 20px;padding:18px;background:var(--panel);border:1px solid var(--line-2);border-radius:14px;box-shadow:0 24px 60px -18px rgba(0,0,0,.55);pointer-events:auto;transform-origin:top left;animation:pop .16s ease}
@keyframes pop{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
.sheet h4{margin:4px 10px 6px;font:500 11px var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--soft2)}
.sheet a{display:block;padding:9px 10px;border-radius:9px;text-decoration:none}
.sheet a:hover,.sheet a.on{background:var(--raised)}
.sheet a b{display:block;font:600 16px var(--tight);letter-spacing:-.01em}
.sheet a span{display:block;color:var(--soft);font-size:13px;line-height:1.35;margin-top:1px}

/* page furniture */
main{padding-block:32px 40px}
.eyebrow,.kicker{font:500 12px var(--mono);letter-spacing:.2em;text-transform:uppercase;color:var(--soft);margin:0 0 16px}
h1{font:600 60px/1.02 var(--tight);letter-spacing:-.035em;text-wrap:balance}
h2{font:600 26px var(--tight);letter-spacing:-.02em}
.lede{margin-top:16px;max-width:56ch;color:var(--soft);font-size:18px;line-height:1.5;text-wrap:pretty}
.lede b{color:var(--text);font-weight:600}
.pg-head{padding-top:28px}
.sec{margin-top:56px}
.sec-h{display:flex;align-items:baseline;justify-content:space-between;gap:24px;flex-wrap:wrap;margin-bottom:18px}
.sec-h p{margin-top:4px;color:var(--soft);font-size:14.5px}
.more{color:var(--soft);font-size:14px;text-decoration:none;white-space:nowrap}
.more:hover{color:var(--text)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:22px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.note{color:var(--soft2);font-size:13px;line-height:1.5}

/* state symbols */
.sym{width:19px;height:19px;flex:none;overflow:visible}
.sym-cool .arw{animation:drop 2.2s ease-in-out infinite}
@keyframes drop{0%{transform:translateY(-4px);opacity:0}35%,60%{transform:none;opacity:1}100%{transform:translateY(4px);opacity:0}}
.sym-rise .arw{animation:rise 2.2s ease-in-out infinite}
@keyframes rise{0%{transform:translateY(4px);opacity:0}35%,60%{transform:none;opacity:1}100%{transform:translateY(-4px);opacity:0}}
.sym-flat .ln{animation:breathe 2.6s ease-in-out infinite;transform-box:fill-box;transform-origin:center}
@keyframes breathe{0%,100%{transform:scaleX(1)}50%{transform:scaleX(.6)}}

/* pill tabs */
.seg{display:inline-flex;gap:4px;border:1px solid var(--line);border-radius:999px;padding:4px;max-width:100%;overflow-x:auto;scrollbar-width:none}
.seg::-webkit-scrollbar{display:none}
.seg button{border:0;background:none;color:var(--soft);font:500 13.5px var(--font);padding:7px 14px;border-radius:999px;cursor:pointer;white-space:nowrap}
.seg button:hover{color:var(--text)}
.seg button.on{background:var(--lime);color:var(--on-lime)}
.ctl{display:flex;gap:10px;flex-wrap:wrap;align-items:center}

/* token avatar */
.av{position:relative;flex:none;width:32px;height:32px;border-radius:50%;background:var(--raised);display:grid;place-items:center;overflow:hidden;font:600 12px var(--tight);color:var(--soft)}
.av img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.av.s{width:24px;height:24px;font-size:10px}

/* ranked list */
.rank{list-style:none;margin:0;padding:0}
.rank li{border-top:1px solid var(--line)}
.rank li:first-child{border-top:0}
.row{display:grid;grid-template-columns:18px 32px minmax(0,1fr) minmax(60px,120px) 84px;align-items:center;gap:12px;width:100%;padding:11px 0;border:0;background:none;text-align:left;cursor:pointer}
.row .n{color:var(--soft2);font-size:12px;font-variant-numeric:tabular-nums}
.row .tks{font:600 15.5px var(--tight);letter-spacing:-.01em}
.row .sub{display:block;color:var(--soft2);font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .val{text-align:right;font:600 15.5px var(--tight);font-variant-numeric:tabular-nums;white-space:nowrap}
.row .val small{display:block;color:var(--soft2);font:400 12px var(--font)}
.bar{height:6px;border-radius:3px;background:var(--raised);overflow:hidden}
.bar i{display:block;height:100%;border-radius:3px;background:var(--up)}
.bar.dn i{background:var(--down)}
.bar.nt i{background:var(--soft)}
.tag{display:inline-block;padding:1px 7px;border:1px solid var(--line-2);border-radius:99px;color:var(--soft);font-size:11px;font-weight:500;white-space:nowrap}
.all{margin-top:14px;border:1px solid var(--line);background:none;color:var(--text);font:500 14px var(--font);padding:9px 16px;border-radius:8px;cursor:pointer}
.all:hover{border-color:var(--line-2)}
.empty{padding:26px 0;color:var(--soft2);font-size:14px}

/* tables */
.tbl{width:100%;border-collapse:collapse;font-size:14px}
.tbl th{padding:0 0 12px 16px;border-bottom:1px solid var(--line);color:var(--soft);font-weight:500;font-size:13px;text-align:right;white-space:nowrap}
.tbl td{padding:13px 0 13px 16px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tbl th:first-child,.tbl td:first-child{padding-left:0;text-align:left}
.tbl td b{font-weight:600}
.scroll{overflow-x:auto}
.up{color:var(--up-ink)}.dn{color:var(--down-ink)}.mut{color:var(--soft2)}
.nb{white-space:nowrap}
/* the small "i": what a number means and how it is scored, for readers who want it */
.info{flex:none;display:inline-grid;place-items:center;width:16px;height:16px;padding:0;border:1px solid var(--line-2);border-radius:50%;background:none;color:var(--soft);font:500 10px/1 var(--mono);cursor:pointer}
.info:hover,.info[aria-expanded="true"]{color:var(--text);border-color:var(--soft)}
.pop{display:none;position:absolute;left:18px;right:18px;top:46px;z-index:6;padding:11px 13px;border:1px solid var(--line-2);border-radius:10px;background:var(--bg);box-shadow:0 10px 30px rgba(0,0,0,.35);color:var(--soft);font-size:13px;line-height:1.45}
.pop.on{display:block}
@media (hover:hover){*:has(>.k>.info:hover)>.pop{display:block}}
/* a 0-100 score as a level, in the site's ASCII */
.am{position:relative;height:26px;margin-top:16px}
.am pre{position:absolute;inset:0;margin:0;font:11px/13px var(--mono);white-space:pre;overflow:hidden;letter-spacing:0}
.am-t{color:var(--soft2);opacity:.6}
.am-f{color:var(--lime-ink)}

/* tool pages: controls, stand-out cards, a ranked list with the picked token's detail beside it */
.pg-ctl{margin-top:26px}
.chip{border:1px solid var(--line);border-radius:999px;background:none;color:var(--soft);font:500 13.5px var(--font);padding:10px 15px;cursor:pointer}
.chip.on{background:var(--raised);color:var(--text)}
.look{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.lk{display:block;width:100%;padding:18px 20px;border:1px solid var(--line);border-radius:var(--r);background:var(--panel);text-align:left;cursor:pointer}
.lk:hover{border-color:var(--line-2)}
.lk-t{display:flex;align-items:center;gap:10px;font:600 16px var(--tight);letter-spacing:-.01em}
.lk-l{display:block;margin-top:12px;font:500 11px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--soft2)}
.lk-x{display:block;margin-top:5px;font-size:15px;line-height:1.4}
.md{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:28px;align-items:start}
.md-list .sec-h{align-items:center;margin-bottom:12px}
.md-sum{color:var(--soft);font-size:14px}
.md-list .panel{padding:8px 22px}
.md-list .row{grid-template-columns:18px 32px minmax(0,1fr) minmax(40px,150px) 124px;padding:12px;margin:0 -12px;width:calc(100% + 24px);border-radius:10px}
.md-list .row:hover{background:var(--hover)}
.md-list li.sel>.row{background:var(--raised)}
.md-list li.sel,.md-list li.sel+li{border-top-color:transparent}
.row .star{color:var(--lime-ink);margin-right:5px;font-size:11px}
.row .sub.tg{color:var(--soft)}
.insp{position:sticky;top:calc(env(safe-area-inset-top,0px) + 88px);padding:22px 24px}
.i-h{display:flex;align-items:center;gap:12px}
.i-h .av{width:40px;height:40px;font-size:14px}
.i-h b{display:block;font:600 22px/1.2 var(--tight);letter-spacing:-.02em}
.i-h small{color:var(--soft2);font-size:13px}
.i-h a{margin-left:auto;color:var(--soft);text-decoration:none;font-size:13px;white-space:nowrap}
.i-h a:hover{color:var(--text)}
.i-k{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin-top:18px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.i-k div{padding:14px 0 13px 16px;border-left:1px solid var(--line);min-width:0}
.i-k div:first-child{border-left:0;padding-left:0}
.i-k span{display:block;color:var(--soft);font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.i-k b{display:block;margin-top:3px;font:600 22px var(--tight);letter-spacing:-.02em;font-variant-numeric:tabular-nums;white-space:nowrap}
.i-s{margin-top:18px;font-size:14.5px;line-height:1.45}
.i-s+.i-s{margin-top:6px}
.i-t{margin-top:22px;font:500 11px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--soft2)}
.cg{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:9px 12px;align-items:center;margin-top:12px;font-size:13.5px}
.cg span:first-child{color:var(--soft)}
.cg b{font-weight:600;text-align:right;font-variant-numeric:tabular-nums}
.zb{position:relative;height:6px;border-radius:3px;background:var(--raised)}
.zb::after{content:"";position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--line-2)}
.zb i{position:absolute;top:0;bottom:0;border-radius:3px}
.zb i.u{left:50%;background:var(--up)}
.zb i.d{right:50%;background:var(--down)}
.facts{display:flex;gap:6px 22px;flex-wrap:wrap;margin-top:14px;color:var(--soft);font-size:13px}
.facts b{color:var(--text);font-weight:600;font-variant-numeric:tabular-nums}
.det-f{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:20px}
.wbtn{border:1px solid var(--line);border-radius:8px;background:none;color:var(--text);font:500 13px var(--font);padding:7px 12px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center}
.wbtn:hover{border-color:var(--line-2)}
.inl{padding:2px 0 20px}
.inl .i-h{display:none}

/* how it works */
details.how{margin-top:56px;border:1px solid var(--line);border-radius:10px;padding:18px 22px}
details.how summary{cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;font:600 17px var(--tight)}
details.how summary::-webkit-details-marker{display:none}
details.how summary::after{content:"+";color:var(--soft);font:400 20px var(--font)}
details.how[open] summary::after{content:"\2212"}
details.how dl{display:grid;grid-template-columns:180px 1fr;gap:12px 24px;margin-top:18px;max-width:900px;font-size:14px}
details.how dt{font-weight:600}
details.how dd{color:var(--soft)}

footer{margin-top:64px;padding-block:22px 40px;border-top:1px solid var(--line);color:var(--soft);font-size:13px;display:flex;gap:8px 32px;flex-wrap:wrap;justify-content:space-between}
footer span{max-width:70ch}

[data-view][hidden]{display:none!important}
@media (max-width:900px){
  .wrap{padding-left:max(20px,env(safe-area-inset-left,0px));padding-right:max(20px,env(safe-area-inset-right,0px))}
  h1{font-size:44px}
  .hd .wrap{gap:12px;height:60px}
  .stamp,.brand em,.brand u{display:none}
  .brand .rh{color:var(--text)}
  .nav{margin-left:auto}
  .hd-r{margin-left:0}
  .sheet{left:max(20px,env(safe-area-inset-left,0px));right:max(20px,env(safe-area-inset-right,0px));width:auto;grid-template-columns:1fr}
  .grid2{grid-template-columns:1fr}
  .sec{margin-top:44px}
  .panel{padding:18px}
  .look{grid-template-columns:1fr}
  .md{grid-template-columns:1fr}
  .insp{display:none}
  .md-list .row{grid-template-columns:18px 32px minmax(0,1fr) 120px;margin:0 -8px;padding:12px 8px;width:calc(100% + 16px)}
  .md-list .row .bar{display:none}
  .md-list .panel{padding:6px 16px}
  details.how dl{grid-template-columns:1fr;gap:4px 0}
  details.how dd{margin-bottom:12px}
}
@media (max-width:520px){
  h1{font-size:38px}
  .lede{font-size:16.5px}
  .row{grid-template-columns:18px 32px minmax(0,1fr) 84px}
  .row .bar{display:none}
  .nav [data-top="home"]{display:none}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

# Shared helpers every view can use: number formats, token avatars, the Tools menu, segmented controls, watchlist.
JS = r"""
window.SOB=(function(){
 const fm=x=>{const a=Math.abs(x);return a>=1e9?'$'+(a/1e9).toFixed(2)+'B':a>=1e8?'$'+Math.round(a/1e6)+'M':a>=1e6?'$'+(a/1e6).toFixed(1)+'M':a>=1e4?'$'+Math.round(a/1e3)+'K':a>=1e3?'$'+(a/1e3).toFixed(1)+'K':'$'+Math.round(a)};
 const signed=x=>(x>=0?'+':'−')+fm(x);
 const pct=(x,d)=>x==null?'—':(x>=0?'+':'−')+Math.abs(x).toFixed(d==null?(Math.abs(x)<10?1:0):d)+'%';
 const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
 let TOK={};
 const av=(sym,cls)=>{const t=TOK[sym]||{};return `<span class="av ${cls||''}" aria-hidden="true">${esc(sym.slice(0,1))}${t.img?`<img src="${esc(t.img)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">`:''}</span>`};
 const seg=(el,opts,cur,on)=>{el.innerHTML=opts.map(([v,l])=>`<button type="button" data-v="${v}" class="${v===cur?'on':''}" aria-pressed="${v===cur}">${l}</button>`).join('');
  el.onclick=e=>{const b=e.target.closest('button');if(b&&b.dataset.v!==cur)on(b.dataset.v)}};
 const KEY='sob-rh-watch';
 const watch={get(){try{return JSON.parse(localStorage.getItem(KEY)||'[]')}catch(e){return []}},
  toggle(s){const w=new Set(this.get());w.has(s)?w.delete(s):w.add(s);try{localStorage.setItem(KEY,JSON.stringify([...w]))}catch(e){}return w.has(s)}};
 const day=iso=>{const m=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];return m[+iso.slice(5,7)-1]+' '+(+iso.slice(8,10))};
 /* a 0-100 score as a level in ASCII: ascii() leaves a placeholder, fit() draws it to the width it was given */
 const ascii=(score,rows)=>`<div class="am" aria-hidden="true" data-score="${score}" data-rows="${rows||2}"><pre class="am-t"></pre><pre class="am-f"></pre></div>`;
 let CW=0;
 const fit=scope=>{(scope||document).querySelectorAll('.am').forEach(el=>{const tr=el.children[0],fl=el.children[1];if(!el.clientWidth)return;
   if(!CW){const c=document.createElement('canvas').getContext('2d');c.font=getComputedStyle(tr).font;CW=c.measureText('MMMMMMMMMM').width/10||6.6}
   const R='.:-=+*#%',cols=Math.max(10,Math.floor(el.clientWidth/CW)),e=Math.round(+el.dataset.score/100*(cols-1)),rows=+el.dataset.rows||2;let f='',t='';
   for(let c=0;c<cols;c++){if(c<=e){f+=R.charAt(Math.max(0,Math.min(7,Math.floor((1-c/(e+1))*8))));t+=' '}else{f+=' ';t+=(c%2?':':'.')}}
   const rep=x=>Array(rows).fill(x).join('\n');tr.textContent=rep(t);fl.textContent=rep(f)})};
 return {fm,signed,pct,esc,av,seg,watch,day,ascii,fit,tokens(t){TOK=t},tok:s=>TOK[s]||{}};
})();
(function(){
 const btn=document.getElementById('tools-btn'),menu=document.getElementById('tools-menu');
 if(!btn)return;
 const set=o=>{menu.hidden=!o;btn.setAttribute('aria-expanded',o)};
 btn.addEventListener('click',e=>{e.stopPropagation();set(menu.hidden)});
 document.addEventListener('click',e=>{if(!menu.hidden&&!e.target.closest('.sheet'))set(false)});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!menu.hidden){set(false);btn.focus()}});
 menu.addEventListener('click',e=>{if(e.target.closest('a'))set(false)});
 const pop=b=>document.getElementById(b.getAttribute('aria-controls'));
 const shut=except=>document.querySelectorAll('.info[aria-expanded="true"]').forEach(x=>{if(x!==except){x.setAttribute('aria-expanded','false');const p=pop(x);if(p)p.classList.remove('on')}});
 document.addEventListener('click',e=>{const b=e.target.closest('.info');shut(b);if(!b)return;const o=b.getAttribute('aria-expanded')==='true',p=pop(b);b.setAttribute('aria-expanded',String(!o));if(p)p.classList.toggle('on',!o)});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')shut(null)});
 const th=document.getElementById('theme-btn');
 if(th){const R=document.documentElement;
  try{if(localStorage.getItem('sob-theme')==='light')R.classList.add('light')}catch(e){}
  th.addEventListener('click',()=>{const l=R.classList.toggle('light');try{localStorage.setItem('sob-theme',l?'light':'dark')}catch(e){};window.dispatchEvent(new Event('sob:theme'))})}
})();
"""

# Inter and Inter Tight are embedded, so nothing loads from another host. The site build (sob-rh-dashboard,
# build_v4.py) puts the site's embedded @font-face block, and the page's share and analytics tags, in place of this
# marker; a page opened without that step falls back to the system font.
FONTS = "<!--sob-fonts-->"
CHEV = '<svg width="10" height="6" viewBox="0 0 10 6" fill="none" aria-hidden="true"><path d="M1 1l4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'


THEME_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor" stroke="none"/></svg>'


def header(active, stamp, href):
    """active: 'home' or a tool id. href(id) gives the link for a view."""
    cols, on = "", ' class="on"'
    for group, items in TOOLS:
        cols += f"<div><h4>{group}</h4>" + "".join(
            f'<a href="{href(i)}" data-nav="{i}"{on if i == active else ""}><b>{html.escape(n)}</b><span>{html.escape(d)}</span></a>'
            for i, n, d in items) + "</div>"
    tool = active != "home"
    return f"""<header class="hd"><div class="wrap">
<a class="brand" href="{href('home')}" data-nav="home" aria-label="{SITE}, by State of Blocks">{MARK}<em style="font-style:normal">State of Blocks</em><u></u><span class="rh">Robinhood Chain</span></a>
<nav class="nav" aria-label="Main">
<a href="{href('home')}" data-nav="home" data-top="home"{'' if tool else on}>Overview</a>
<button type="button" id="tools-btn" aria-expanded="false" aria-controls="tools-menu" data-top="tools"{on if tool else ''}><span id="tools-label">{'Tools' if not tool else TOOL_NAMES.get(active, 'Tools')}</span>{CHEV}</button>
</nav>
<div class="hd-r"><span class="stamp">{html.escape(stamp)}</span><button class="theme" id="theme-btn" type="button" aria-label="Switch theme">{THEME_ICON}</button></div>
</div><div class="menu" id="tools-menu" hidden><div class="wrap"><div class="sheet" role="menu" aria-label="Tools">{cols}</div></div></div></header>"""


DISCLOSURE = "Disclosure: the author holds PRIORS, BUN, RAETO and PONS."      # as on the live site


def footer():
    return ('<footer class="wrap"><span>State of Robinhood Chain, by State of Blocks. Research by that1618guy, Marcus. '
            'Independent research. Not affiliated with or endorsed by Robinhood. <a href="/methodology">Methodology</a></span>'
            '<span>Sources: Robinhood Chain, read directly (stock-token supply, the ERC-8004 agent registry); GeckoTerminal '
            '(prices, pool volume, holders); DefiLlama (TVL, fees, revenue, DEX volume); Priors (self-reported credit data). '
            f'Updated daily, through the last full day (UTC). {DISCLOSURE}</span></footer>')


def _data(view):
    if view.get("data") is None:
        return ""
    blob = json.dumps(view["data"], separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/json" id="{view["id"]}-data">{blob}</script>'


def _tokens(tokens):
    blob = json.dumps(tokens or {}, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/json" id="sob-tokens">{blob}</script>'


BOOT = "SOB.tokens(JSON.parse(document.getElementById('sob-tokens').textContent));"


def page(view, stamp, tokens=None, pretty=True):
    """One view as a full page for the live site. Links are /flows style; pretty=False gives flows.html."""
    href = (lambda i: "/" if i == "home" else f"/{i}") if pretty else (lambda i: "index.html" if i == "home" else f"{i}.html")
    title = SITE if view["id"] == "home" else f"{view['title']} · {SITE}"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{html.escape(title)}</title><meta name="description" content="{html.escape(view.get('desc', ''))}">
{FONTS}<style>{CSS}{view.get('css', '')}</style></head><body>
{header(view['id'], stamp, href)}
<main class="wrap" data-view="{view['id']}">{view['body']}</main>
{footer()}
{_tokens(tokens)}{_data(view)}
<script>{JS}{BOOT}{view.get('js', '')}</script></body></html>"""


def bundle(views, stamp, tokens=None, title=SITE):
    """All views in one file for previews. The link's #name picks the view; no <html>/<body> so it publishes as a page too."""
    href = lambda i: f"#{i}"
    names = json.dumps({v["id"]: v["title"] for v in views})
    sections = "".join(f'<main class="wrap" data-view="{v["id"]}" hidden>{v["body"]}</main>' for v in views)
    route = r"""
(function(){
 const N=%s,views=[...document.querySelectorAll('[data-view]')];
 function show(){
  let id=(location.hash||'#home').slice(1);if(!N[id])id='home';
  views.forEach(v=>v.hidden=v.dataset.view!==id);
  const tool=id!=='home';
  document.querySelector('[data-top="home"]').classList.toggle('on',!tool);
  document.getElementById('tools-btn').classList.toggle('on',tool);
  document.getElementById('tools-label').textContent=tool?N[id]:'Tools';
  document.querySelectorAll('.sheet a').forEach(a=>a.classList.toggle('on',a.dataset.nav===id));
  window.scrollTo(0,0);
  window.dispatchEvent(new CustomEvent('sob:view',{detail:id}));
 }
 window.addEventListener('hashchange',show);show();
})();""" % names
    return f"""<meta name="claude:viewport-fit-sides" content="cover">
<title>{html.escape(title)}</title>
{FONTS}<style>{CSS}{''.join(v.get('css', '') for v in views)}</style>
{header('home', stamp, href)}
{sections}
{footer()}
{_tokens(tokens)}{''.join(_data(v) for v in views)}
<script>{JS}{BOOT}{''.join(v.get('js', '') for v in views)}{route}</script>"""


# ---- helpers for text and small static graphics, shared by every builder ----
MINUS = "−"


def fm(x):
    a = abs(x)
    return (f"${a/1e9:.2f}B" if a >= 1e9 else f"${a/1e6:.0f}M" if a >= 1e8 else f"${a/1e6:.1f}M" if a >= 1e6
            else f"${a/1e3:.0f}K" if a >= 1e4 else f"${a/1e3:.1f}K" if a >= 1e3 else f"${a:,.0f}")


def signed(x):
    return ("+" if x >= 0 else MINUS) + fm(x)


def pct(x, d=None):
    if x is None:
        return "—"
    d = (1 if abs(x) < 10 else 0) if d is None else d
    return ("+" if x >= 0 else MINUS) + f"{abs(x):.{d}f}%"


def dshort(iso):
    import datetime as dt
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def avatar(sym, tokens, cls=""):
    t = (tokens or {}).get(sym) or {}
    img = (f'<img src="{html.escape(t["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">'
           if t.get("img") else "")
    return f'<span class="av {cls}" aria-hidden="true">{html.escape(sym[:1])}{img}</span>'


def spark(vals, w=120, h=34, cls="spark"):
    """A small line with its last point marked. vals: numbers, oldest first."""
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    span = (hi - lo) or 1
    pts = [(2 + i * (w - 6) / (len(v) - 1), 3 + (hi - x) / span * (h - 6)) for i, x in enumerate(v)]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    ex, ey = pts[-1]
    return (f'<svg class="{cls}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<path d="{path}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="2.6" fill="var(--ink)"/></svg>')
