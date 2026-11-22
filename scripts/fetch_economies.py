#!/usr/bin/env python3
"""DefiLlama data for v4, on top of what fetch.py already pulls.

Adds daily revenue, DEX volume and value to holders (trend lines on the key numbers) and the
TVL history of every app with stock-token markets (moved from the main page to stock-tokens.html).
Standard library only. Writes into DATA_DIR.
"""
import json, os, sys, time, urllib.request

DATA = os.environ.get("DATA_DIR", "data/latest")
B, CHAIN = "https://api.llama.fi", "Robinhood%20Chain"
STOCK_APPS = os.path.join(os.path.dirname(__file__), "stock_apps.json")   # curated list: slug, name, type, core
FILES = {
    "rh_rev_chart.json": f"{B}/overview/fees/{CHAIN}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=false&dataType=dailyRevenue",
    "rh_dex_chart.json": f"{B}/overview/dexs/{CHAIN}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=false",
    "rh_hold_chart.json": f"{B}/overview/fees/{CHAIN}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true&dataType=dailyHoldersRevenue",
}


def get(url):
    for k in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "stateofblocks-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except Exception as e:
            err = e
            time.sleep(3 + 5 * k)
    raise RuntimeError(f"DefiLlama request failed: {url}: {err}")


def main():
    os.makedirs(DATA, exist_ok=True)
    for name, url in FILES.items():
        json.dump(get(url), open(os.path.join(DATA, name), "w"))
    apps = json.load(open(STOCK_APPS))
    out = {}
    for a in apps:
        j = get(f"{B}/protocol/{a['slug']}")
        ser = (j.get("chainTvls", {}).get("Robinhood Chain") or {}).get("tvl") or []
        out[a["slug"]] = [[p["date"], p["totalLiquidityUSD"]] for p in ser[-200:]]
        time.sleep(0.3)
    json.dump(out, open(os.path.join(DATA, "stock_apps_tvl.json"), "w"))
    print(f"[economies] DefiLlama extras saved; TVL history for {len(out)} stock-token apps")


if __name__ == "__main__":
    main()
