# Published results

Everything here was produced by `herdbench build all` from public data, after the pre-analysis addendum was
committed and timestamped. Every file's SHA-256 is in [`manifest.json`](manifest.json). The report that reads
these files is [`docs/results.md`](../docs/results.md).

| Path | Content | Licence |
|---|---|---|
| `truth_panel_summary.json` | Coverage and suppression of the truth panel | CC BY 4.0 |
| `gates.json` | Reconciliation, Gate 0 and kill criterion 1 | CC BY 4.0 |
| `funding_check.json` | The funding inputs checked against Hyperliquid's own API (`herdbench check`) | CC BY 4.0 |
| `score_summary.json` | Markets, block length, positive control, kill criteria 3 and 4 | CC BY 4.0 |
| `directional_*.csv`, `unsigned_*.csv`, `positive_control.csv`, `robustness_cohorts.csv` | Secondary statistics for Hyperliquid-native proxies | CC BY 4.0 |
| `sensitivity_complete_flows.csv` | The flow proxies rescored on complete seven-day windows only (deviation 4) | CC BY 4.0 |
| `liquidation_target.csv`, `liquidation_delta_rho.json` | Second target: liquidations in the next 7 days | CC BY 4.0 |
| `cot_bridges*.csv` | CFTC positioning against matching perpetuals (description only) | CC BY 4.0 |
| `binance/` | Every statistic that involves Binance Vision data, including the primary family | CC BY-NC-SA 4.0 |
| `finra/` | Stock transfer statistics from FINRA short interest | FINRA terms (source and owner FINRA; non-commercial) |

The two Parquet files listed in the manifest, `truth_panel.parquet` and `proxies.parquet`, are stored once, in
[`src/herdbench/data/`](../src/herdbench/data/), where the package loads them from; a test checks that they are
byte-identical to the hashes in the manifest.

Attribution for CC BY 4.0 material: "HerdBench (Mohammed Zeeshan), https://github.com/z33shxn/herdbench". The funding
inputs are Ironflow's `hyperliquid-funding` dataset (CC BY 4.0).
