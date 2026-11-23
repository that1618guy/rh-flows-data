"""Token facts the pages share: logo, market cap, fully diluted value and pool liquidity for every token in the
universe, from GeckoTerminal in one call per 30 tokens. Stock tokens are skipped: token views are crypto only.

Usage: token_meta.py <universe.json> <out.json> [logos_dir]
With logos_dir, each logo is saved there once and the pages point at /logos/<SYMBOL>.<ext> on our own site, so
they never depend on another host. Publish that folder as /logos.
On a failed fetch the previous file is kept, so a GeckoTerminal outage never drops logos or market caps.
"""
import json, os, re, sys, time, urllib.error, urllib.request

GT = "https://api.geckoterminal.com/api/v2/networks/robinhood/tokens/multi/"
NAMES = {"STANDARD": "The Standard Reserve"}          # display names where the token's own name is just its symbol


def get(url, tries=6):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "sob-meta"})
            return json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code == 404 or k == tries - 1:
                raise
            time.sleep(20 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(10 * (k + 1))


def build(universe):
    toks = [t for t in universe if t.get("category") != "stocks"]
    out = {}
    for i in range(0, len(toks), 30):
        part = toks[i:i + 30]
        d = get(GT + ",".join(t["address"] for t in part))
        by = {x["attributes"]["address"].lower(): x["attributes"] for x in d["data"]}
        for t in part:
            a = by.get(t["address"].lower())
            if not a:
                continue
            f = lambda k: float(a[k]) if a.get(k) else None
            img = a.get("image_url")
            out[t["symbol"]] = {"name": NAMES.get(t["symbol"]) or t.get("name") or t["symbol"], "address": t["address"].lower(),
                                "img": img if img and img.startswith("https://") and "missing" not in img else None,
                                "price": f("price_usd"), "fdv": f("fdv_usd"), "mcap": f("market_cap_usd"),
                                "liq": f("total_reserve_in_usd") or 0.0}
        time.sleep(2.5)
    return out


def _kind(raw):
    """File extension from the image's first bytes, or None if it isn't an image we serve."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:2] == b"\xff\xd8":
        return ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    if raw[:3] == b"GIF":
        return ".gif"
    return None


def save_logos(meta, logos_dir, web_path="/logos/"):
    """Save each token's logo once and point its img at our own copy. A logo that can't be fetched keeps its
    remote address; a token with no logo shows its first letter on the page."""
    os.makedirs(logos_dir, exist_ok=True)
    have = {os.path.splitext(f)[0]: f for f in os.listdir(logos_dir) if not f.endswith(".tmp")}
    saved = 0
    for sym, m in meta.items():
        url, key = m.get("img"), re.sub(r"[^A-Za-z0-9_-]", "", sym)
        if not url or not key:
            continue
        if key in have:
            m["img"] = web_path + have[key]; saved += 1
            continue
        try:
            req = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0 (sob-meta)", "accept": "image/*"})
            raw = urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            continue
        ext = _kind(raw)
        if not ext or not 200 <= len(raw) <= 2_000_000:
            m["img"] = None            # not a usable image (e.g. a blank 87-byte PNG): the page shows the first letter
            continue
        tmp = os.path.join(logos_dir, key + ".tmp")
        open(tmp, "wb").write(raw); os.replace(tmp, os.path.join(logos_dir, key + ext))
        m["img"] = web_path + key + ext; saved += 1
        time.sleep(0.3)
    return saved


def size(m):
    """What a token is worth for comparing flows: market cap, or fully diluted value where no market cap is
    published. A market cap far below FDV is kept (most of the supply isn't trading)."""
    return (m.get("mcap") if m.get("mcap") and m["mcap"] > 1000 else m.get("fdv")) or 0.0


if __name__ == "__main__":
    uni = json.load(open(sys.argv[1]))
    try:
        m = build(uni)
        if len(m) < 0.8 * len([t for t in uni if t.get("category") != "stocks"]):
            raise RuntimeError(f"only {len(m)} tokens came back")
    except Exception as e:
        if os.path.exists(sys.argv[2]):
            print(f"::warning title=Token facts::kept the previous file ({e})"); raise SystemExit(0)
        raise
    own = save_logos(m, sys.argv[3]) if len(sys.argv) > 3 else 0
    tmp = sys.argv[2] + ".tmp"
    json.dump(m, open(tmp, "w"), separators=(",", ":")); os.replace(tmp, sys.argv[2])
    print(f"token facts: {len(m)} tokens, {sum(1 for x in m.values() if x['img'])} with a logo, {own} saved on the site")
