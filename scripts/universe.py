"""The token list: which Robinhood Chain tokens the token pages follow. Kept by rule, CAP tokens at most.

  in      a token nobody listed gets in after IN_NIGHTS nights in a row with IN_LIQ+ of pool liquidity (the figure the
          pages already use: GeckoTerminal's total reserve for the token) and IN_TRADERS+ wallets trading it that day
  out     a listed token goes after OUT_NIGHTS nights in a row with under OUT_LIQ of liquidity, or with fewer than
          OUT_WALLETS wallets trading it, or with its volume only passing through (the test that already hides a
          token from every page: inflow.PASS_MAX)
  full    at CAP, a newcomer takes the quietest token's place when it is clearly busier (BUSIER times the wallets)
  never   stock tokens (Robinhood's own factory), ETH and dollar tokens, a symbol another listed token or a stock
          already uses, and symbols that aren't plain letters and digits

Everything slow runs at the end of the nightly job, after the pages and the Discord post, so the list never holds
them up: the night is counted there, and a change made there (a token in, a token out) shows on the pages from the
next run and is dated that day. Only the hand overrides are applied before the read, which takes no time.

A new token is read in apart from the rest first (data/flows_cache/warming/): 30 days of flows and a full holder scan.
It joins data/flows_universe.json only once that history is complete, so a newcomer never leaves a page short for
the others. A busy old token can take several nights (its holder scan carries on from where it stopped). At most
MAX_NEW tokens start each night.

Three files:
  data/flows_universe.json   the list the engine and the pages read. Written here; same format as before.
  data/universe_state.json   this script's memory: streaks, tokens being read in, what changed and when.
  data/universe_rules.json   the hand overrides: "add" (addresses: skip the wait; acted on once, so a token added
                             by hand can still be dropped by the rule later unless it is also under "keep"),
                             "keep" (never dropped), "block" (never listed; an address or a symbol),
                             "category" and "name" by symbol.

Usage:
  universe.py rules --universe U --state S --rules R --cache C [--docs docs] [--asof D]
        before the chain read, no network: apply "block", "category" and "name", and take in hand edits to the list
  universe.py scan --universe U --state S --rules R --cache C [--docs docs] [--asof D] [--dry-run]
        once a night, at the end of the run: look for candidates, count the night, drop and admit
  universe.py warm --universe U --state S --rules R --cache C [--budget-min 60] [--asof D]
        after scan: read the admitted tokens' history, and list the ones that are complete
  universe.py show --universe U --state S --rules R
        print the list, what is waiting and what changed lately
"""
import argparse, datetime as dt, glob, gzip, json, os, re, shutil, statistics, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rh
import flows_engine as FE
import flows_model as FM
import inflow as IN

CAP = 40                         # tokens on the list at most
IN_LIQ = IN.MIN_LIQ              # to get in: the liquidity a token needs to show on the pages at all ...
IN_TRADERS = 100                 # ... and this many wallets trading it in the day ...
IN_NIGHTS = 3                    # ... this many nights in a row
OUT_LIQ = 25_000                 # to go: under this much liquidity ...
OUT_WALLETS = 10                 # ... or fewer wallets than this trading it ...
OUT_NIGHTS = 7                   # ... this many nights in a row
MAX_NEW = 2                      # tokens that start being read in per night
MAX_WARMING = 3                  # tokens being read in at once
WARM_NIGHTS = 7                  # a token is given up on after this many nights with no progress on its history
COOL_DAYS = 14                   # a token that was dropped or given up on can't come back for this long
GRACE = 7                        # nights a newcomer can't be the one that makes room
BUSIER = 1.5                     # full list: the newcomer needs this many times the quietest token's wallets
FLOW_DAYS, HOLDER_DAYS = 30, 40  # the history the pages need: flows_engine's window and holders_state's
FLOW_BUDGET = 25                 # minutes of a night's read-in that may go to flows; the rest is for holder scans
EVENTS_KEPT = 200

FACTORY = "0x4783c67b63de2b358ac5951a7d41f47a38f3c046"                    # Robinhood's stock-token factory
T_DEPLOYED = "0xd9b0c6a1c0de228715ad0fa09f3259686ee84f8cc675e03ef7e47a9cdafa76d6"   # (token, name, symbol)
# ETH and dollar tokens are what the others are priced in, never tokens to follow
NOT_TOKENS = {rh.ZERO, "0x0bd7d308f8e1639fab988df18a8011f41eacad73", "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
              "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34", "0x40858070814a57fdf33a613ae84fe0a8b4a874f7",
              "0xcec185eb182c47d1ba1efc84e6959e18cd620be4"}
NOT_SYMBOLS = {"ETH", "WETH", "MMETH", "CBBTC", "WBTC", "BTC", "DAI"}
SYMBOL = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,9}$")       # it becomes a folder and a file name
RESERVED = {"holders", "logos", "capital", "warming", "scan", "codes", "edges"}
CATEGORIES = {"memes", "agents", "ecosystem"}
POOL_SORTS = ("h24_tx_count_desc", "h24_volume_usd_desc")
POOL_PAGES = 10                  # GeckoTerminal lists 10 pages of 20 pools per order
POOL_BUDGET = 10 * 60            # seconds the pool list may take; a slow night stops early and counts what it has
GT_PAUSE = 4.0                   # extra seconds between this script's GeckoTerminal calls: a refused call costs a minute
FOREVER = "9999-12-31"


def log(*a):
    print("token list:", *a, flush=True)


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, obj):
    """Write JSON the way the list file has always been written, and leave a file alone when nothing in it changed."""
    text = json.dumps(obj, indent=1)
    try:
        if open(path).read() == text:
            return
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def shift(d, n):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=n)).isoformat()


def gap(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def yesterday():
    return (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()


# ------------------------------------------------------------------------------------------- what a token may be
def clean_name(name, symbol):
    """A token's own name is whatever its maker typed, and it ends up inside the pages: keep letters, digits, spaces
    and a little punctuation, nothing that could be markup."""
    s = "".join(c for c in (name or "") if c.isalnum() or c in " .-!$+")
    s = re.sub(r"\s+", " ", s).strip()[:32].strip()
    return s or symbol


def symbol_ok(sym):
    s = sym or ""
    return bool(SYMBOL.match(s)) and s.lower() not in RESERVED and s.upper() not in NOT_SYMBOLS and "USD" not in s.upper()


def sane(addr):
    """A contract the engine can work with. A token's decimals are whatever its contract answers, and the engine
    raises 10 to that power: a made-up answer would stall the read."""
    try:
        return 0 <= rh.decimals(addr) <= 36
    except Exception:
        return True                                   # the chain didn't answer: the read-in will find out


def new_state():
    return {"version": 1, "night": None, "tokens": {}, "stocks": {"to": 0, "tokens": {}}, "events": [], "seq": 0, "hand": []}


def rules_of(raw):
    raw = raw or {}
    low = lambda xs: {str(x).strip().lower() for x in (xs or [])}
    return {"add": [str(x).strip().lower() for x in raw.get("add") or []], "keep": low(raw.get("keep")),
            "block": low(raw.get("block")), "category": {k.upper(): v for k, v in (raw.get("category") or {}).items()},
            "name": {k.upper(): v for k, v in (raw.get("name") or {}).items()}}


def named(rule, addr, sym):
    """True if a keep or block list names this token, by address or by symbol."""
    return addr in rule or (sym or "").lower() in rule


# ------------------------------------------------------------------------------------------------ what's out there
def stock_tokens(st):
    """{address: symbol} of every token Robinhood's stock-token factory has deployed. Read once, then only the new
    blocks each night."""
    sk = st.setdefault("stocks", {"to": 0, "tokens": {}})
    head = rh.head()
    for chunk in rh.iter_logs(FACTORY, [T_DEPLOYED], sk["to"] + 1, head, step=800000):
        for l in chunk:
            d = l["data"][2:]

            def s(off):
                n = int(d[off * 2:off * 2 + 64], 16)
                return bytes.fromhex(d[off * 2 + 64:off * 2 + 64 + n * 2]).decode("utf-8", "replace")
            sk["tokens"][("0x" + d[24:64]).lower()] = s(int(d[128:192], 16))
    sk["to"] = head
    return sk["tokens"]


def top_pools():
    """{token address: {"symbol", "name", "traders", "first"}} from the chain's busiest pools: the most wallets
    trading the token in one of its pools over 24 hours, and the day its oldest listed pool was made.
    Returns (tokens, pages read): the caller decides whether enough came back to count the night."""
    out, pages, t0 = {}, 0, time.time()
    for sort in POOL_SORTS:
        for page in range(1, POOL_PAGES + 1):
            if time.time() - t0 > POOL_BUDGET:
                log(f"pool list: out of time after {pages} pages")
                return out, pages
            try:
                d = FE.gt(f"{FE.GT}/networks/robinhood/pools?page={page}&sort={sort}&include=base_token,quote_token")
            except Exception as e:
                log(f"pool list {sort} page {page} didn't come back ({type(e).__name__})")
                break
            pages += 1
            time.sleep(GT_PAUSE)
            inc = {x["id"]: x["attributes"] for x in d.get("included", []) if x.get("type") == "token"}
            for p in d.get("data", []):
                a, r = p["attributes"], p["relationships"]
                tr = ((a.get("transactions") or {}).get("h24") or {}).get("unique_traders") or 0
                made = (a.get("pool_created_at") or "")[:10]
                for side in ("base_token", "quote_token"):
                    t = inc.get(r[side]["data"]["id"])
                    if not t or not t.get("address"):
                        continue
                    x = out.setdefault(t["address"].lower(), {"symbol": t.get("symbol") or "", "name": t.get("name") or "",
                                                              "traders": 0, "first": made or None})
                    x["traders"] = max(x["traders"], int(tr))
                    if made and (not x["first"] or made < x["first"]):
                        x["first"] = made
            if len(d.get("data", [])) < 20:
                break
    return out, pages


def token_facts(addrs):
    """{address: {"symbol", "name", "liq"}} for the addresses GeckoTerminal knows. liq is the token's total reserve in
    USD, the same figure token_meta.py gives the pages."""
    out, addrs = {}, sorted(set(addrs))
    for i in range(0, len(addrs), 30):
        d = FE.gt(f"{FE.GT}/networks/robinhood/tokens/multi/" + ",".join(addrs[i:i + 30]))
        time.sleep(GT_PAUSE)
        for x in d.get("data", []):
            a = x["attributes"]
            out[a["address"].lower()] = {"symbol": a.get("symbol") or "", "name": a.get("name") or "",
                                         "liq": float(a["total_reserve_in_usd"]) if a.get("total_reserve_in_usd") else 0.0}
    return out


def wallets(tdir, n=7):
    """(latest cached day, wallets that traded on each of the last n cached days) for one token folder: the Flows
    page's wallets, so bots and contracts aren't counted. (None, []) if the folder has nothing."""
    try:
        with gzip.open(os.path.join(tdir, "head.json.gz"), "rt") as f:
            kinds = json.load(f).get("kinds", {})
        days = sorted(fn[:10] for fn in os.listdir(tdir) if fn[:4].isdigit() and fn.endswith(".json.gz"))[-n:]
    except (OSError, ValueError):
        return None, []
    out = []
    for d in days:
        try:
            with gzip.open(os.path.join(tdir, f"{d}.json.gz"), "rt") as f:
                rows = json.load(f)["rows"]
        except (OSError, ValueError, KeyError):
            continue
        out.append(sum(1 for a, r in rows.items() if r[2] <= FM.BOT_TRADES_PER_DAY and kinds.get(a, "eoa") in FM.WALLET_KINDS))
    return (days[-1] if days else None), out


def passing(tdir):
    """% of the token's 7-day wallet volume that came from wallets holding none before and none after: the figure
    the pages hide a token on (inflow.passed_through). None if it can't be worked out."""
    try:
        t = FM.load_token(tdir)
        w = FM.token_window(t, [shift(t["head"]["day"], -i) for i in range(7)][::-1])
        return (w or {}).get("passthrough")
    except Exception:
        return None


def busy(tdir):
    """Wallets trading the token on a typical day of its last 7 cached."""
    _, w = wallets(tdir)
    return statistics.median(w) if w else 0


# ------------------------------------------------------------------------------------------------ the list itself
class Book:
    """The list, the state and the rules, with the few moves a token can make."""

    def __init__(self, a, night):
        self.a, self.night = a, night
        self.uni = load(a.universe, [])
        self.st = {**new_state(), **(load(a.state) or {})}
        self.first_run = not self.st["tokens"]
        self.rules = rules_of(load(a.rules, {}))
        self.cache = a.cache
        self.docs = getattr(a, "docs", None)
        self.dry = getattr(a, "dry_run", False)
        self.said = []
        self.when = night                             # the day a change is dated: the first day the pages show it

    # -- lookups
    def tok(self):
        return self.st["tokens"]

    def of(self, status):
        return {k: r for k, r in self.tok().items() if r["status"] == status}

    def tdir(self, sym, warming=False):
        return os.path.join(self.cache, "warming", sym) if warming else os.path.join(self.cache, sym)

    def event(self, kind, addr, why=""):
        r = self.tok()[addr]
        self.st["seq"] = self.st.get("seq", 0) + 1         # numbered, so the Discord step knows what it has posted
        self.st["events"].append({"n": self.st["seq"], "d": self.when, "type": kind, "s": r["symbol"], "a": addr, "why": why})
        self.st["events"] = self.st["events"][-EVENTS_KEPT:]
        self.said.append(f"{'added' if kind == 'add' else 'dropped'} {r['symbol']}" + (f" ({why})" if why else ""))

    # -- moves
    def entry(self, addr):
        r = self.tok()[addr]
        sym = r["symbol"]
        cat = self.rules["category"].get(sym.upper())
        cat = cat if cat in CATEGORIES else r.get("category") if r.get("category") in CATEGORIES else "memes"
        return {"symbol": sym, "address": addr, "name": self.rules["name"].get(sym.upper()) or r.get("name") or sym,
                "category": cat}

    def clean(self, sym):
        """Remove everything cached for a symbol, so a token that comes back, or another that takes the symbol,
        starts clean."""
        if self.dry:
            return
        for p in (self.tdir(sym), self.tdir(sym, True)):
            shutil.rmtree(p, ignore_errors=True)
        junk = [os.path.join(self.cache, "holders", f"{sym}.state.json.gz"),
                os.path.join(self.cache, "warming", "holders", f"{sym}.state.json.gz"),
                os.path.join(self.cache, "warming", "scan", f"{sym}.json.gz"),
                os.path.join(self.cache, "warming", "scan", f".{sym}.part.json.gz"),
                os.path.join(self.cache, "warming", "scan", f".{sym}.part.at")]
        junk += glob.glob(os.path.join(self.cache, "logos", f"{sym}.*"))
        if self.docs:
            junk += glob.glob(os.path.join(self.docs, "logos", f"{sym}.*"))
        for p in junk:
            try:
                os.remove(p)
            except OSError:
                pass

    def retire(self, addr, why, until=None, tell=True):
        """Take a token off the list (or out of the read-in) and keep it away until `until`."""
        r = self.tok()[addr]
        was = r["status"]
        if was == "listed" and tell:
            self.event("drop", addr, why)
        self.uni = [e for e in self.uni if e["address"].lower() != addr]
        r.update(status="out", why=why, until=until or shift(self.night, COOL_DAYS), good=0, bad=0)
        if was in ("listed", "warming"):
            self.clean(r["symbol"])
        return r

    def admit(self, addr, how):
        r = self.tok()[addr]
        r.update(status="warming", since=self.night, how=how, good=0, bad=0)
        r.pop("until", None); r.pop("why", None)
        log(f"{r['symbol']} starts being read in ({'added by hand' if how == 'hand' else 'passed the rule'})")

    def quietest(self):
        """(address, wallets on a typical day) of the listed token that would make room: the quietest one that isn't
        kept by hand and isn't new. (None, 0) if none can."""
        best = (None, 0)
        for addr, r in self.of("listed").items():
            if named(self.rules["keep"], addr, r["symbol"]):
                continue
            if r.get("added") and gap(r["added"], self.night) < GRACE:
                continue
            b = busy(self.tdir(r["symbol"]))
            if best[0] is None or b < best[1]:
                best = (addr, b)
        return best

    def write(self):
        if self.dry:
            log("dry run: nothing written")
            return
        cats, names = self.rules["category"], self.rules["name"]
        for e in self.uni:                            # a hand override in the rules file reaches the list the same night
            if cats.get(e["symbol"].upper()) in CATEGORIES:
                e["category"] = cats[e["symbol"].upper()]
            if names.get(e["symbol"].upper()):
                e["name"] = names[e["symbol"].upper()]
        save(self.a.universe, self.uni)
        save(self.a.state, self.st)


def reconcile(B):
    """Make the state agree with the list file, which stays the source of truth for what is listed: an entry the
    state doesn't know was put there by hand, and a listed token missing from the file was taken out by hand."""
    tok, seen = B.tok(), set()
    have = [e for e in B.uni if os.path.exists(os.path.join(B.tdir(e["symbol"]), "head.json.gz"))]
    cache_ok = len(have) >= max(1, len(B.uni) // 2)
    for e in list(B.uni):
        addr = e["address"].lower(); seen.add(addr)
        r = tok.get(addr)
        if r and r["status"] == "listed":
            # listed by this script, and its folder is gone (the cache was lost): read it in again, without a word
            if r.get("how") != "start" and cache_ok and e not in have:
                B.uni.remove(e)
                B.admit(addr, r.get("how") or "rule")
                r["again"] = True
            continue
        tok[addr] = {"symbol": e["symbol"], "name": e.get("name") or e["symbol"], "status": "listed", "good": 0, "bad": 0,
                     "how": "start" if B.first_run else "hand", "added": None if B.first_run else B.night,
                     "category": e.get("category")}
        # put there by hand with nothing cached: read it in apart first, like any newcomer
        if not B.first_run and cache_ok and e not in have:
            B.uni.remove(e)
            B.admit(addr, "hand")
    for addr, r in list(tok.items()):
        if r["status"] == "listed" and addr not in seen:
            B.retire(addr, "taken off by hand", tell=False)
            log(f"{r['symbol']} is no longer in the list file: taken off")


def tidy(B):
    """Holder files left behind by tokens that aren't on the list (INU, dropped by hand before this script): the
    Holders page would go on naming them. Nothing is removed if the list looks wrong."""
    listed = {e["symbol"] for e in B.uni}
    stray = [p for p in glob.glob(os.path.join(B.cache, "holders", "*.state.json.gz"))
             if os.path.basename(p).split(".")[0] not in listed]
    if B.dry or len(listed) < 5 or len(stray) > max(1, len(listed) // 4):
        return
    for p in stray:
        os.remove(p)
        log(f"removed the holder file of {os.path.basename(p).split('.')[0]}, which is not on the list")


def apply_block(B):
    """block from the rules file: a listed token goes at once, and is dated today (it is off tonight's pages when this
    runs before the read)."""
    for addr, r in list(B.tok().items()):
        if r["status"] != "out" and named(B.rules["block"], addr, r["symbol"]):
            B.retire(addr, "blocked", until=FOREVER)


def apply_add(B, facts, stocks):
    """add from the rules file. facts: token_facts() for the addresses in "add"."""
    tok, R = B.tok(), B.rules
    done = B.st["hand"] = [x for x in B.st.get("hand", []) if x in R["add"]]      # an address is acted on once
    for addr in R["add"]:
        r = tok.get(addr)
        if addr in done:
            continue
        if r and r["status"] in ("listed", "warming"):
            done.append(addr); continue
        f = facts.get(addr)
        if not re.match(r"^0x[0-9a-f]{40}$", addr) or not f:
            log(f"add: {addr} isn't a token GeckoTerminal knows on this chain, skipped"); continue
        why = barred(B, addr, f["symbol"], stocks)
        if not why and not sane(addr):
            why = "its contract gives decimals no token has"
        if why:
            log(f"add: {f['symbol'] or addr} can't be listed ({why}), skipped"); continue
        if len(B.of("warming")) >= MAX_WARMING:
            log(f"add: {f['symbol']} waits, {MAX_WARMING} tokens are already being read in"); continue
        tok[addr] = {"symbol": f["symbol"], "name": clean_name(f["name"], f["symbol"]), "liq": round(f["liq"])}
        B.admit(addr, "hand")
        done.append(addr)


def barred(B, addr, sym, stocks):
    """Why this token can never be listed as it stands, or '' if it can."""
    if addr in NOT_TOKENS:
        return "ETH or a dollar token"
    if addr in stocks:
        return "a stock token"
    if not symbol_ok(sym):
        return "its symbol isn't plain letters and digits"
    if named(B.rules["block"], addr, sym):
        return "blocked"
    if sym.upper() in {s.upper() for s in stocks.values()}:
        return "its symbol is a stock token's"
    for a2, r in B.tok().items():
        if a2 != addr and r["symbol"].upper() == sym.upper():
            if r["status"] in ("listed", "warming"):
                return f"{r['symbol']} is already a listed token"
            if r["status"] == "out" and r.get("until", "") > B.night and r.get("why") != "copy":
                return f"{r['symbol']} was another token until lately"
    return ""


def count_night(B, pools, facts, stocks, counted_pools):
    """One night's bookkeeping. pools: top_pools(), facts: token_facts(), counted_pools: whether enough of the pool
    list came back to judge candidates by it."""
    tok, night = B.tok(), B.night
    # listed tokens: a bad night is thin, dead quiet, or volume that only passes through
    for addr, r in B.of("listed").items():
        f = facts.get(addr)
        last, w = wallets(B.tdir(r["symbol"]), 1)
        thin = f["liq"] < OUT_LIQ if f else None
        fresh = bool(w and last and gap(last, night) <= 2)
        quiet = w[-1] < OUT_WALLETS if fresh else None
        through = (passing(B.tdir(r["symbol"])) or 0) >= IN.PASS_MAX if fresh else None
        if f:
            r["liq"] = round(f["liq"])
        if w:
            r["traders"] = w[-1]
        if thin or quiet or through:
            r["bad"] = r.get("bad", 0) + 1
            r["why"] = (f"under ${OUT_LIQ // 1000}K of liquidity" if thin else f"fewer than {OUT_WALLETS} wallets trading it" if quiet
                        else "volume only passing through")
        elif thin is None or quiet is None:
            continue                                  # a figure is missing tonight: the streak neither grows nor resets
        else:
            r["bad"] = 0; r.pop("why", None)
        if r["bad"] >= OUT_NIGHTS and not named(B.rules["keep"], addr, r["symbol"]):
            B.retire(addr, f"{r['why']} for {OUT_NIGHTS} nights")
    # tokens that served their time away
    for addr, r in list(tok.items()):
        lifted = r.get("until") == FOREVER and r.get("why") == "blocked" and not named(B.rules["block"], addr, r["symbol"])
        if r["status"] == "out" and (r.get("until", "") <= night or lifted):
            del tok[addr]
    if not counted_pools:
        log("the pool list didn't come back: candidates aren't counted tonight, their streaks stand")
        return
    # candidates
    for addr, r in list(B.of("watch").items()):
        if addr not in pools or addr not in facts:
            del tok[addr]                             # fell out of the busiest pools: the streak is over
    for addr, p in pools.items():
        f = facts.get(addr)
        r = tok.get(addr)
        if (r and r["status"] != "watch") or not f:
            continue
        good = f["liq"] >= IN_LIQ and p["traders"] >= IN_TRADERS and not barred(B, addr, f["symbol"], stocks)
        if not good:
            tok.pop(addr, None)
            continue
        r = tok.setdefault(addr, {"status": "watch", "good": 0, "since": night})
        r.update(symbol=f["symbol"], name=clean_name(f["name"], f["symbol"]), liq=round(f["liq"]), traders=p["traders"],
                 first=p.get("first"), good=r.get("good", 0) + 1)


def admit_some(B, stocks):
    """Start reading in the candidates that have served their nights: busiest first, MAX_NEW a night. Of two tokens
    with one symbol the older pool wins and the other is forgotten for a while."""
    by_sym = {}
    for addr, r in sorted(B.of("watch").items(), key=lambda x: (x[1].get("first") or "9", -x[1]["traders"], x[0])):
        by_sym.setdefault(r["symbol"].upper(), []).append(addr)
    for addrs in by_sym.values():                       # the oldest pool keeps the symbol, even if it is a night behind
        for addr in addrs[1:]:
            r = B.tok()[addr]
            r.update(status="out", why="copy", until=shift(B.night, COOL_DAYS))
            log(f"{r['symbol']} at {addr}: a newer token with the same symbol as the one at {addrs[0]}, passed over")
    started = 0
    q_addr, q_busy = None, 0
    for addr, r in sorted(B.of("watch").items(), key=lambda x: -x[1]["traders"]):
        if r["good"] < IN_NIGHTS:
            continue
        if started >= MAX_NEW or len(B.of("warming")) >= MAX_WARMING:
            log(f"{r['symbol']} has passed the rule and waits its turn"); continue
        if barred(B, addr, r["symbol"], stocks):
            del B.tok()[addr]; continue
        if not sane(addr):
            r.update(status="out", why="odd contract", until=FOREVER)
            log(f"{r['symbol']} at {addr}: its contract gives decimals no token has, passed over"); continue
        if len(B.of("listed")) + len(B.of("warming")) >= CAP:
            if B.of("warming"):                         # one place is being taken at a time
                log(f"{r['symbol']} has passed the rule and waits: the list is full and a newcomer is already taking a place"); continue
            if q_addr is None:
                q_addr, q_busy = B.quietest()
            if q_addr is None or r["traders"] < BUSIER * max(q_busy, 1):
                log(f"{r['symbol']} has passed the rule, but the list is full and it isn't busier than the quietest token on it")
                continue
        B.admit(addr, "rule"); started += 1


# ----------------------------------------------------------------------------------------------------- commands
def cmd_rules(a):
    night = a.asof or yesterday()
    B = Book(a, night)
    reconcile(B)
    apply_block(B)
    B.write()
    summary(B)


def cmd_scan(a):
    night = a.asof or yesterday()
    B = Book(a, night)
    reconcile(B)
    apply_block(B)                                      # (already done by `rules` in the nightly run: a no-op there)
    B.when = shift(night, 1)                            # tonight's pages are already built: what changes here shows tomorrow
    again = B.st.get("night") == night
    try:
        stocks = stock_tokens(B.st)
    except Exception as e:
        stocks = B.st.get("stocks", {}).get("tokens", {})
        log(f"couldn't read the stock-token factory ({type(e).__name__}), using the {len(stocks)} known")
    if again:
        log(f"{night} is already counted")
        pools, pages, facts = {}, 0, {}
        try:
            facts = token_facts(B.rules["add"]) if B.rules["add"] else {}
        except Exception:
            pass
    else:
        pools, pages = top_pools()
        counted = pages >= POOL_PAGES            # at least half the list
        near = [x for x, p in pools.items() if p["traders"] >= IN_TRADERS and x not in NOT_TOKENS and x not in stocks]
        known = [x for x, r in B.tok().items() if r["status"] in ("listed", "watch", "warming")]
        try:
            facts = token_facts(set(near) | set(known) | set(B.rules["add"]))
        except Exception as e:
            log(f"token facts didn't come back ({type(e).__name__}): the night isn't counted")
            B.write(); return
        count_night(B, pools, facts, stocks, counted)
        if counted:
            admit_some(B, stocks)
        B.st["night"] = night
    apply_add(B, facts, stocks)
    tidy(B)
    B.write()
    summary(B)


def run(cmd, timeout=None):
    print("$", " ".join(os.path.basename(c) if c.endswith(".py") else c for c in cmd[1:]), flush=True)
    try:
        return subprocess.run(cmd, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        log(f"stopped after {timeout // 60} minutes")
        return -1


def complete(tdir, night):
    """True when a token folder has every day the pages use, a head snapshot on `night` and a price that day."""
    days = [shift(night, -i) for i in range(FLOW_DAYS)]
    if not all(os.path.exists(os.path.join(tdir, f"{d}.json.gz")) for d in days):
        return False
    try:
        with gzip.open(os.path.join(tdir, "head.json.gz"), "rt") as f:
            if json.load(f)["day"] != night:
                return False
        with gzip.open(os.path.join(tdir, f"{night}.json.gz"), "rt") as f:
            return bool(json.load(f).get("price_close"))
    except (OSError, ValueError, KeyError):
        return False


def progress(B, sym, night):
    """How far a token's read-in has got: [days of the 30 cached, the holder scan's last block]. It is given up on
    only when this stops moving, so a token with tens of millions of transfers gets the nights it needs."""
    tdir = B.tdir(sym, True)
    days = sum(1 for i in range(FLOW_DAYS) if os.path.exists(os.path.join(tdir, f"{shift(night, -i)}.json.gz")))
    at = load(os.path.join(B.cache, "warming", "scan", f".{sym}.part.at"), {}) or {}
    return [days, at.get("cursor", 0)]


def holder_state(B, sym, uni_path, night, deadline):
    """The token's holder state as of `night`, from a full scan of its transfers. The scan stops at `deadline` and
    carries on from there next run. Path, or None if it isn't finished."""
    wdir = os.path.join(B.cache, "warming")
    sdir, hdir = os.path.join(wdir, "scan"), os.path.join(wdir, "holders")
    os.makedirs(sdir, exist_ok=True); os.makedirs(hdir, exist_ok=True)
    out = os.path.join(hdir, f"{sym}.state.json.gz")

    def at():
        try:
            with gzip.open(out, "rt") as f:
                return json.load(f).get("asof")
        except (OSError, ValueError):
            return None
    if at() and at() < night:                          # scanned on an earlier night: bring it forward from the flows read
        run([sys.executable, os.path.join(HERE, "holders_state.py"), "roll", out, wdir])
    if at() == night:
        return out
    left = deadline - time.time()
    if left < 120:
        log(f"{sym}: no time left tonight for its holder scan")
        return None
    edges = os.path.join(B.cache, "edges.json")
    if os.path.exists(edges) and not os.path.exists(os.path.join(sdir, "edges.json")):
        shutil.copy(edges, os.path.join(sdir, "edges.json"))
    scan = os.path.join(sdir, f"{sym}.json.gz")
    if os.path.exists(scan):
        os.remove(scan)
    run([sys.executable, os.path.join(HERE, "holders_scan.py"), "--universe", uni_path, "--out", sdir, "--codes",
         os.path.join(B.cache, "codes.json"), "--days", str(HOLDER_DAYS), "--asof", night, "--only", sym, "--workers", "1",
         "--budget-min", f"{left / 60:.1f}"], timeout=int(left) + 900)
    if not os.path.exists(scan):
        return None
    rc = run([sys.executable, os.path.join(HERE, "holders_state.py"), "init", scan, os.path.join(sdir, "edges.json"), out])
    try:
        os.remove(scan)
    except OSError:
        pass
    return out if rc == 0 and at() == night else None


def merge_codes(B):
    """Wallet kinds looked up while reading a newcomer in join the main table, so nothing is looked up twice."""
    main_p = os.path.join(B.cache, "codes.json")
    main = load(main_p, {})
    n = len(main)
    for p in (os.path.join(B.cache, "warming", "codes.json"), os.path.join(B.cache, "warming", "scan", "codes.json")):
        for k, v in (load(p, {}) or {}).items():
            main.setdefault(k, v)
    if len(main) != n:
        tmp = main_p + ".tmp"
        json.dump(main, open(tmp, "w")); os.replace(tmp, main_p)


def cmd_warm(a):
    night = a.asof or yesterday()
    B = Book(a, night)
    B.when = shift(night, 1)                            # tonight's pages are already built: what changes here shows tomorrow
    deadline = time.time() + a.budget_min * 60
    W = B.of("warming")
    if not W:
        log("no token is being read in"); summary(B); return
    wdir = os.path.join(B.cache, "warming")
    os.makedirs(wdir, exist_ok=True)
    edges = os.path.join(B.cache, "edges.json")
    if os.path.exists(edges):                           # the days' first blocks are already known: don't look them up again
        shutil.copy(edges, os.path.join(wdir, "edges.json"))
    for addr, r in W.items():                           # put by hand in the list with a folder already: carry on from it
        if os.path.isdir(B.tdir(r["symbol"])) and not os.path.isdir(B.tdir(r["symbol"], True)):
            shutil.move(B.tdir(r["symbol"]), B.tdir(r["symbol"], True))
    uni_path = os.path.join(wdir, "universe.json")
    save(uni_path, [B.entry(addr) for addr in W])
    flow_min = min(FLOW_BUDGET, a.budget_min / 2)
    run([sys.executable, os.path.join(HERE, "flows_engine.py"), "--universe", uni_path, "--cache", wdir, "--days", str(FLOW_DAYS),
         "--max-new", str(FLOW_DAYS), "--budget-min", str(flow_min), "--workers", "3", "--asof", night],
        timeout=int((flow_min + 15) * 60))           # the budget stops new days being started; this stops a read that hangs
    for addr, r in sorted(W.items(), key=lambda x: -(x[1].get("traders") or 0)):
        sym, tdir = r["symbol"], B.tdir(r["symbol"], True)
        done = complete(tdir, night)
        state = holder_state(B, sym, uni_path, night, deadline) if done else None
        if progress(B, sym, night) != r.get("at"):          # it moved tonight: the clock starts again
            r["at"], r["moved"] = progress(B, sym, night), night
        late = gap(r.get("moved") or r["since"], night) >= WARM_NIGHTS and not r.get("again")   # after a lost cache: as long as it takes
        if not done:
            if late:
                B.retire(addr, "its history couldn't be read"); log(f"{sym}: given up on after {WARM_NIGHTS} nights without progress")
            else:
                log(f"{sym}: history not complete yet, carries on next run")
            continue
        if not state:
            if late:
                B.retire(addr, "its holders couldn't be read"); log(f"{sym}: given up on after {WARM_NIGHTS} nights without progress")
            else:
                at = load(os.path.join(B.cache, "warming", "scan", f".{sym}.part.at"), {}) or {}
                log(f"{sym}: holder scan not finished"
                    + (f" ({at['cursor'] / at['head'] * 100:.0f}% of the chain, {at['transfers']:,} transfers so far)" if at.get("head") else "")
                    + ", carries on next run")
            continue
        t = FM.load_token(tdir)
        w7 = FM.token_window(t, [shift(night, -i) for i in range(7)][::-1]) or {}
        if r.get("how") != "hand" and (w7.get("passthrough") or 0) >= IN.PASS_MAX:
            B.retire(addr, "its volume passes through wallets that keep none")
            log(f"{sym}: {w7['passthrough']:.0f}% of its volume passes through wallets that keep none, not listed"); continue
        if len(B.of("listed")) >= CAP:
            q, qb = B.quietest()
            mine = busy(tdir)
            if q is None or (r.get("how") != "hand" and mine < BUSIER * max(qb, 1)):
                B.retire(addr, "the list is full")
                log(f"{sym}: the list is full and it isn't busier than the quietest token on it, not listed"); continue
            B.retire(q, f"made room for {sym}", until=shift(night, COOL_DAYS))
        if B.dry:
            log(f"{sym}: would be listed"); continue
        log(f"{sym}: history complete, on the pages from the next run")
        merge_codes(B)
        shutil.rmtree(B.tdir(sym), ignore_errors=True)
        shutil.move(tdir, B.tdir(sym))
        os.makedirs(os.path.join(B.cache, "holders"), exist_ok=True)
        shutil.move(state, os.path.join(B.cache, "holders", f"{sym}.state.json.gz"))
        r.update(status="listed", added=r.get("added") or night, good=0, bad=0)
        r.pop("at", None); r.pop("moved", None)
        B.uni.append(B.entry(addr))
        if not r.pop("again", False):
            B.event("add", addr, "added by hand" if r.get("how") == "hand" else "")
        B.write()                                       # now, so a run cut short can't leave the folder without its entry
    B.write()
    if not B.of("warming") and not B.dry:               # nobody left to read in: clear the side folder
        shutil.rmtree(wdir, ignore_errors=True)
    summary(B)


def summary(B):
    L, W, C = B.of("listed"), B.of("warming"), B.of("watch")
    names = lambda d: ", ".join(sorted(r["symbol"] for r in d.values())) or "none"
    log(f"{len(L)} of {CAP} listed; being read in: {names(W)}; on watch: "
        + (", ".join(f"{r['symbol']} {r['good']}/{IN_NIGHTS}" for r in sorted(C.values(), key=lambda r: (-r['good'], r['symbol']))) or "none")
        + f"; changes tonight: {'; '.join(B.said) or 'none'}")


def cmd_show(a):
    B = Book(a, a.asof or yesterday())
    L = sorted(({**r, "a": addr} for addr, r in B.of("listed").items()), key=lambda r: -(r.get("liq") or 0))
    print(f"Listed ({len(L)} of {CAP}), by liquidity:")
    for r in L:
        flag = f"   {r['bad']} of {OUT_NIGHTS} nights {r.get('why', '')}" if r.get("bad") else ""
        kept = "   kept by hand" if r["symbol"].lower() in B.rules["keep"] or r.get("a") in B.rules["keep"] else ""
        print(f"  {r['symbol']:10s} ${r.get('liq') or 0:>11,.0f}  {r.get('traders') if r.get('traders') is not None else '?':>5} wallets{flag}{kept}")
    for title, d in (("Being read in", B.of("warming")), ("On watch", B.of("watch"))):
        print(f"{title}:" + ("" if d else " none"))
        for addr, r in sorted(d.items(), key=lambda x: -(x[1].get("good") or 0)):
            more = f"since {r['since']}" if r["status"] == "warming" else f"{r['good']} of {IN_NIGHTS} nights"
            print(f"  {r['symbol']:10s} ${r.get('liq') or 0:>11,.0f}  {r.get('traders') or 0:>5} wallets   {more}   {addr}")
    print("Lately:" + ("" if B.st["events"] else " nothing"))
    for e in B.st["events"][-12:]:
        print(f"  {e['d']}  {'added  ' if e['type'] == 'add' else 'dropped'} {e['s']}" + (f"  ({e['why']})" if e.get("why") else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("rules", "scan", "warm", "show"))
    ap.add_argument("--universe", required=True); ap.add_argument("--state", required=True); ap.add_argument("--rules", required=True)
    ap.add_argument("--cache", default="data/flows_cache"); ap.add_argument("--docs")
    ap.add_argument("--asof"); ap.add_argument("--budget-min", type=float, default=60); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    {"rules": cmd_rules, "scan": cmd_scan, "warm": cmd_warm, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
