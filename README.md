# rh-flows-data

A daily read of Robinhood Chain, published as static pages on GitHub Pages:
https://that1618guy.github.io/rh-flows-data/

| File | What |
|---|---|
| `home.html` | The site's home page: is money coming onto Robinhood Chain or leaving (`capital.py`, `inflow.py`, `build_home.py`) |
| `flows.html` | Who's buying and selling RH tokens, by wallet size (`scripts/flows_engine.py`, `build_flows_page.py`) |
| `smart.html` | What the most profitable wallets hold and trade (`smart_money.py`, `build_smart_page.py`) |
| `holders.html` | Holder strength, a 0–100 score per token (`holders_state.py`, `holders_model.py`, `build_holders_page.py`) |
| `valuation.json` | What the market pays for RH-native projects' revenue (`revenue_multiple.py`); the site builds the section with `build_valuation_page.py` |
| `logos/` | Token logos (`token_meta.py`), served by the site as `/logos/` |
| `status.json` | Each page's date, the logos, and any step that failed |
| `site-data/` | Raw public data for the site: DefiLlama (`fetch.py`, `fetch_economies.py`, `derive.py`) and stock tokens from the chain RPC and GeckoTerminal (`fetch_stocks.py`), with its own `status.json` |

The pages are whole pages in the site's shell (`site_shell.py`: header, Tools menu, footer, shared styles). The site build adds its embedded fonts and share tags at the `<!--sob-fonts-->` marker.

**Sources.** Robinhood Chain's public RPC, GeckoTerminal and DefiLlama. No keys or secrets.

**Schedule.** `.github/workflows/daily.yml` runs at 00:40 UTC, once the previous UTC day is complete, and can be run by hand. The chain read is cached between runs (Actions cache); an empty cache starts from the `cache-seed` release, and the stablecoin mint and burn state from the `capital-seed` release.

`.github/workflows/site-data.yml` runs at 01:30 UTC, as soon as the previous UTC day has closed, with a retry at 04:30. It publishes `site-data/` only when every fetch succeeds and DefiLlama has the previous day, so the files there are always one consistent set. The set and the stock-token cache are assets on the `site-data` release, replaced on each run, so they don't grow the git history; `.github/workflows/pages.yml` deploys `docs/` plus the set to GitHub Pages.

Describes on-chain behaviour; not investment advice.
