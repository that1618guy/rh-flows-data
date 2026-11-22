# rh-flows-data

A daily read of Robinhood Chain, published as static pages on GitHub Pages:
https://that1618guy.github.io/rh-flows-data/

| File | What |
|---|---|
| `flows.html` | Who's buying and selling RH tokens, by wallet size (`scripts/flows_engine.py`, `build_flows_page.py`) |
| `holders.html` | Holder strength, a 0–100 score per token (`holders_state.py`, `holders_model.py`, `build_holders_page.py`) |
| `smart.html` | What the most profitable wallets did yesterday (`smart_money.py`, `build_smart_page.py`) |
| `valuation.html` | What the market pays for RH-native projects' revenue (`revenue_multiple.py`, `build_valuation_page.py`) |
| `status.json` | Each page's as-of date, what it shows, and any step that failed |
| `site-data/` | Raw public data for the site: DefiLlama (`fetch.py`, `fetch_economies.py`, `derive.py`) and stock tokens from the chain RPC and GeckoTerminal (`fetch_stocks.py`), with its own `status.json` |

The pages are content fragments (their own CSS, data and script) that a site wraps in its own page shell.

**Sources.** Robinhood Chain's public RPC, GeckoTerminal and DefiLlama. No keys or secrets.

**Schedule.** `.github/workflows/daily.yml` runs at 00:40 UTC, once the previous UTC day is complete, and can be run by hand. The chain read is cached between runs (Actions cache); an empty cache starts from the `cache-seed` release.

`.github/workflows/site-data.yml` runs at 01:30 UTC, as soon as the previous UTC day has closed, with a retry at 04:30. It publishes `site-data/` only when every fetch succeeds and DefiLlama has the previous day, so the files there are always one consistent set. The set and the stock-token cache are assets on the `site-data` release, replaced on each run, so they don't grow the git history; `.github/workflows/pages.yml` deploys `docs/` plus the set to GitHub Pages.

Describes on-chain behaviour; not investment advice.
