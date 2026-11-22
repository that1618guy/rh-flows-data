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

The pages are content fragments (their own CSS, data and script) that a site wraps in its own page shell.

**Sources.** Robinhood Chain's public RPC, GeckoTerminal and DefiLlama. No keys or secrets.

**Schedule.** `.github/workflows/daily.yml` runs at 00:40 UTC, once the previous UTC day is complete, and can be run by hand. The chain read is cached between runs (Actions cache); an empty cache starts from the `cache-seed` release.

Describes on-chain behaviour; not investment advice.
