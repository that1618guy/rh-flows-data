"""Pull everything the dashboard needs from DefiLlama's public API into DATA_DIR."""
import json, os, sys, time, urllib.request

DATA = os.environ.get("DATA_DIR", "data/latest")
B = "https://api.llama.fi"
CHAIN = "Robinhood%20Chain"
FILES = {
    "rh_dailyFees.json": f"{B}/overview/fees/{CHAIN}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyFees",
    "rh_dailyRevenue.json": f"{B}/overview/fees/{CHAIN}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue",
    "rh_dailyHoldersRevenue.json": f"{B}/overview/fees/{CHAIN}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyHoldersRevenue",
    "rh_fees_chart.json": f"{B}/overview/fees/{CHAIN}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=false&dataType=dailyFees",
    "rh_dex.json": f"{B}/overview/dexs/{CHAIN}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true",
    "rh_tvl.json": f"{B}/v2/historicalChainTvl/{CHAIN}",
    "protocols.json": f"{B}/protocols",
    "all_fees.json": f"{B}/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyFees",
}


def get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "sob-rh-dashboard"}), timeout=120) as r:
                return json.load(r)
        except Exception:  # DefiLlama occasionally times out; retry with backoff
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def main():
    os.makedirs(DATA, exist_ok=True)
    for name, url in FILES.items():
        d = get(url)
        if name == "protocols.json":  # keep only Robinhood Chain projects: the full list is ~9 MB
            d = [p for p in d if "Robinhood Chain" in (p.get("chains") or [])]
        json.dump(d, open(os.path.join(DATA, name), "w"))
        print("fetched", name)


if __name__ == "__main__":
    sys.exit(main())
