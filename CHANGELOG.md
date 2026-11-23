# Changelog

All notable changes to HerdBench are recorded in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Changing a registered analysis parameter is a new study, not a routine change. Departures from the registered
design are listed in [docs/deviations.md](docs/deviations.md).

## [Unreleased]

## [1.0.0] - 2026-11-19

First release. The design was registered on 2026-11-18, before any proxy was compared with the truth.
Results: see [docs/results.md](docs/results.md).

### Added

#### Pre-registration and timestamp

- `docs/preregistration.md`, the frozen design. Every analysis parameter appears in its tables and in
  `herdbench.config.PREREG`, a frozen dataclass. `tests/test_preregistration.py` fails if the two disagree in
  either direction.
- `docs/timestamps/prereg-v1.txt`, which records the registering commit (`f205501`) and the SHA-256 of
  `docs/preregistration.md`, with an OpenTimestamps proof (`prereg-v1.txt.ots`).
- `docs/deviations.md`, which lists every departure from the registered design with its reason and effect.
- A scoring guard. `herdbench build addendum` renders the pre-analysis addendum
  (`docs/preregistration_addendum.md`) from the built truth panel: the entity map's SHA-256, market-maker
  counts, coverage and suppression. `herdbench build score` refuses to run without it, and with `--strict` the
  current entity map must also match the recorded hash.

#### Data sources (`herdbench.sources`, `herdbench.fetch`)

- A reader for the public Hugging Face mirror of Hyperliquid's all-account snapshots and fills, pinned to one
  revision. Snapshots are copied verbatim and checked against the SHA-256 the Hub publishes. Fill files are read
  column by column through HTTP range requests and reduced to 14 columns, with a provenance record per file.
- Readers for Ironflow's CC BY 4.0 copy of Hyperliquid's settled funding and premium (pinned revision),
  Hyperliquid's public info API, Binance Vision (metrics, 5-minute klines and funding), FINRA short interest and
  CFTC Commitments of Traders. None needs an account or a key.
- An optional Coinalyze client for cross-source checks, with its key in `COINALYZE_API_KEY`. No published result
  depends on it.
- One HTTP client for every source: retries with exponential backoff, `Retry-After` handling, and request
  spacing within public rate limits.
- `herdbench fetch`, which downloads every input (about 25 GB) and reuses files already present.

#### Truth pipeline (`herdbench.truth`)

- One all-account snapshot per UTC date; when a date has two, the earlier capture is used. Protocol accounts (the
  HLP vault, its leader and its child accounts) and builder-dex backstop liquidators are removed from every
  cohort.
- Entities: a master account and its sub-accounts count once. The map comes from the venue's `userRole` and
  `subAccounts` lookups for every market-maker candidate and every top-3 holder of a market with at least $1M of
  open interest. Lookups are resumable and stay local.
- A market-maker rule on entities over the 30 days before each snapshot: at least 80% of traded notional passive,
  at least $10M traded, at least 10 active days, and turnover of at least 50 times the mean gross position. Over
  gaps in the fills, the last known set carries forward for at most 15 days. Four robustness rules vary the maker
  share (0.7, 0.9) and the turnover multiple (20, 100).
- Cohorts: `all`; `non_mm`, the primary crowd; and, inside `non_mm`, `top_av` (top 20% by account value at the
  latest snapshot 8 to 14 days earlier), `trend` and `contrarian` (30-day trend score of at least +0.20 or at
  most −0.20, from at least 5 position changes).
- Truth metrics per snapshot date, market and cohort: `ni`, `ai` and `lwi` (signed); `abs_ni`, `top10_share`,
  `fragile_share` and `coheld_share` (unsigned); and `dtl`, days to liquidate, which is published but never
  scored.
- Disclosure control, applied to publication only: at least 20 entities per cell, the two largest entities
  holding at most 80% of its gross notional, a complement rule for nested cohorts, and rounding (ratios to
  3 decimals, notional to 2 significant figures, entity counts down to a multiple of 10). Robustness cohorts are
  never published.
- Hourly price bars, taker volume and liquidations built from the fills.

#### Proxies (`herdbench.proxies`)

- One timing rule: a snapshot's cutoff is the last full UTC hour at or before its capture, and every proxy uses
  only data stamped before it. Open interest comes from the snapshots one and eight days earlier, never from the
  snapshot being scored.
- Hyperliquid-native proxies: `premium_24h`, `abs_premium_24h`, `funding_24h` and `funding_7d` (each with its
  share of hours pinned at the baseline rate), `momentum_7d`, `oi_change_7d`, `oi_flow_7d`, `liq_intensity_7d`,
  `oi_to_volume_7d` and `comovement_30d`, plus `rv_7d` as a control.
- Binance proxies: `bn_global_ls`, `bn_top_account_ls`, `bn_top_position_ls`, `bn_taker_ls`, `bn_oi_change_7d`
  and `bn_funding_24h`. A ratio whose information is more than 60 minutes old at the cutoff counts as missing.
- The frozen primary panel (`herdbench/data/primary_panel.json`): the 30 largest main-dex markets by open
  interest on the 2025-08-27 snapshot that have a USDT-margined Binance perpetual.

#### Grading (`herdbench.grading`)

- The primary statistic: the within-market Spearman correlation over time, averaged across markets on Fisher's
  z scale, with a circular block bootstrap whose date draws are shared by all proxies. The block length is the
  median Politis–White estimate across the panel's series, clipped to 20 to 40 days.
- The primary family: seven confirmatory proxies against `ni(non_mm)` on common market-days, with Holm
  correction, a rotation null, a two-way fixed-effects slope clustered by market and date, four verdicts
  (confirmed, reversed, uninformative, inconclusive), bootstrap rank intervals, and a half-sample stability
  check (kill criterion 4).
- Secondary statistics for any proxy and truth pair: daily changes, daily cross-sectional Spearman with a
  Fama–MacBeth mean and Newey–West error, an open-interest-weighted mean, a ±60-day placebo, the fixed-effects
  slope, sign agreement with a Wilson interval, and tail lift. Each grid is flagged with Benjamini–Hochberg.
- A positive control (`momentum_7d` against `ni(trend)`), cross-venue agreement for the three Binance ratios, and
  robustness grids for the four market-maker rule variants.
- A second target: liquidations in the 7 days after each capture, scored cross-sectionally, with partial
  correlations controlling for `rv_7d` and an out-of-sample comparison of `fragile_share` with the best unsigned
  proxy.
- Transfer to US single-stock perpetuals (`herdbench.universe` classifies the `xyz` markets): FINRA short
  interest and perp-side proxies against `ni(non_mm)`, paired at settlement and at publication, as two-way
  fixed-effects slopes clustered by stock. p-values are reported only if simulated clustered power reaches 0.80
  (kill criterion 3).
- Futures bridges: CFTC managed-money, leveraged-fund and asset-manager positions against the matching
  perpetuals, week by week, as description only.

#### Gates (`herdbench.gates`)

- Reconciliation, which gates every result: at least 99% of changed position notional between consecutive
  snapshots must equal the net of the fills between the two captures. It is reported for market makers and for
  everyone else.
- Gate 0, which gates every result: maker and taker notional within 1% on at least 99% of market-days.
- Kill criterion 1, reported but not gating: at least 25 US single-stock perpetuals with at least 183 days between
  their first and last archived fill.
- If the reconciliation or Gate 0 fails, every result is labelled "fills incomplete".

#### Pipeline (`herdbench.pipeline`)

- Cached stages that can be rerun one at a time with `herdbench build <stage>`: `panel-selection`, `entities`,
  `truth`, `proxies`, `publish`, `addendum`, `score`, `gates` and `manifest`. `all` runs truth,
  gates, proxies, publish, score and manifest; the gates run right after the truth stage so that a failed gate labels
  every published file, the truth panel and `grade()` reports included.
- `herdbench check` cross-checks the pinned funding inputs against Hyperliquid's own `fundingHistory` on a seeded
  sample of market-hours and writes `results/funding_check.json`.
- Results split by licence: `results/` (CC BY 4.0), `results/binance/` (CC BY-NC-SA 4.0, including the primary
  family) and `results/finra/` (FINRA's terms). `derived/` holds addresses and the entity map and is never
  published.
- `results/manifest.json`: the SHA-256 of every published file, the mirror and funding revisions, and the hashes
  of the pre-registration and the addendum.
- The truth, proxy and scoring stages read only local files. A missing input raises an error that points to
  `herdbench fetch`.

#### Python API (`herdbench`)

- `load_truth_panel()` and `load_proxies()` read the published truth panel and the native proxies from the
  package's data folder, or from the files named by `HERDBENCH_PANEL` and `HERDBENCH_PROXIES`.
- `grade()` scores any signal (long or wide; pandas or polars) against a truth metric and cohort of the published
  panel, with the study's native baselines on exactly the same market-days, and returns a `GradeReport`. Every
  result is labelled "in-sample, published panel".

#### Command line (`herdbench`)

- `info`, `panel` and `grade` work on the published panel. `fetch`, `build`, `check`, `report` and `doctor`
  reproduce and verify the study. Timestamped signals are aligned to the first snapshot whose cutoff is at or after
  each timestamp, so no value is scored against a snapshot cut off before it was known.

#### Report (`herdbench.report`)

- `herdbench report` renders `docs/results.md` from `results/`: gates and kill criteria, truth panel coverage,
  the primary family, positive control and cross-venue agreement, secondary statistics, liquidations, the stock
  transfer, futures bridges, robustness and deviations. Every number is read from `results/`.
- Forest plots as light and dark SVGs, reproducible byte for byte. They need matplotlib (the `report` extra);
  without it the report has tables only.

#### Tests, CI and packaging

- An offline test suite that writes a complete synthetic archive in the real file formats and runs the pipeline
  on it, from the truth panel to the manifest.
- CI on Linux, Windows and macOS with Python 3.10 to 3.13: ruff, mypy, pytest, and a smoke test of the built
  wheel.
- Packaging: Python 3.10 or later, Apache-2.0, typed (`py.typed`), the `report` and `dev` extras, and the
  `herdbench` console script.

[Unreleased]: https://github.com/z33shxn/herdbench/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/z33shxn/herdbench/releases/tag/v1.0.0
