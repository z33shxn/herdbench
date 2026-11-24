# Data

This page lists every input HerdBench reads: what it takes from each source, how it fetches and checks it, the
terms it uses it under, and what it publishes. HerdBench reads seven sources. Six feed the study; the seventh,
Coinalyze, is optional and feeds nothing. Every parameter named here is frozen in
[the pre-registration](preregistration.md) and in `herdbench.config.PREREG`.

Two rules hold for every source:

1. **No input file is redistributed.** Downloads and intermediate tables stay in a local data folder
   ([layout](#on-disk-layout)).
2. **Only aggregates are published, each under a stated licence.** The truth panel holds cohort cells that pass
   disclosure control ([privacy](#privacy-and-disclosure-control)), `proxies.parquet` holds market-level proxies,
   and every other published file holds statistics ([what is published](#what-herdbench-publishes)).

## Sources at a glance

| Source | What HerdBench takes | Terms | Published from it |
|---|---|---|---|
| Hyperliquid archive (Hugging Face mirror) | All-account position snapshots; fills reduced to 14 columns | No licence declared | Truth panel, native proxies, native statistics |
| Ironflow `hyperliquid-funding` (Hugging Face) | Hourly funding rate and premium | CC BY 4.0 | Funding and premium proxies; the statistics that use them |
| Hyperliquid info API | `userRole` and `subAccounts` answers, for the entity map | Public endpoint, no key | Counts and a hash of the map only |
| Binance Vision | 5-minute positioning metrics, 5-minute klines, funding rates | CC BY-NC-SA 4.0 | Statistics only, in `results/binance/` |
| FINRA equity short interest | Twice-monthly short-interest files | FINRA's terms | Summary statistics only, in `results/finra/` |
| CFTC Commitments of Traders | Weekly long and short positions by trader group, 14 contracts | US government work (public domain) | Per-market weekly correlations |
| Coinalyze (optional) | Nothing in the build; optional cross-checks | Coinalyze's API terms | Nothing |

## Sources

### 1. Hyperliquid archive: Hugging Face mirror

- **Repository:** [`craftify2221/hyperliquid-fills-raw`](https://huggingface.co/datasets/craftify2221/hyperliquid-fills-raw),
  revision `de05dc101c819aa68accfefd6647b8bf6de02189` (`PREREG.mirror_revision`). No login and no key. Every
  request names the revision, so a rerun reads the same bytes even after the repository changes.
- **Content:** a republication of Hyperliquid's public node data: all-account position snapshots and daily fill
  files. HerdBench reads these two kinds of file and ignores everything else in the repository.
- **Terms:** the repository declares no licence. HerdBench keeps the files local and never redistributes the
  snapshots or the fills.
- **Code:** `src/herdbench/sources/hfmirror.py`, run by `herdbench fetch --only mirror`.

| Files in the mirror | How HerdBench reads them | Stored as |
|---|---|---|
| `perp_archive/date=YYYY-MM-DD/<block>_<ms>.parquet`: position snapshots; `<ms>` is the capture time in Unix milliseconds | Downloaded whole and kept verbatim. Each file must match the size and SHA-256 that the Hub lists for it. A partial copy resumes with a range request; a copy that fails the check is deleted and fetched once more, then the run stops with an error. A valid local copy is reused. | `raw/hf/perp_archive/...`, same relative path |
| `fills_raw_YYYY-MM-DD.parquet`: one fill file per day | Never downloaded whole. HerdBench reads the Parquet footer, fetches only the byte ranges of 14 columns with HTTP range requests (about a sixth of each file) and checks the row count against the footer. `price`, `size` and `liquidation_mark_px` are cast from decimal to float64. | `raw/hf/fills_slim/fills_YYYY-MM-DD.parquet` (zstd), with a JSON sidecar |

The 14 fill columns (`FILL_COLUMNS`):

```
coin  dex  asset_class  price  size  side  timestamp  direction  address
crossed  twap_id  is_liquidation  liquidation_mark_px  liquidation_method
```

The sidecar records the source path, the revision, the source file's size and published SHA-256, the row and
row-group counts, the bytes transferred and the columns kept. A fill file is never held whole, so its SHA-256
cannot be checked; a day already on disk is read again only when the source SHA-256, the revision or the column
list differs from its sidecar.

From each snapshot HerdBench reads `user`, `market`, `size`, `notional`, `liquidation_price`, `leverage` and
`account_value`. It uses one snapshot per UTC date; when a date has two files, the earlier capture wins.

- **Built from it:** the truth panel (positions grouped into entities and cohorts); per-market hourly prices,
  taker volume, open interest and liquidations, which feed the native proxies; and the two integrity gates, which
  test whether the fills explain every change in position between snapshots and whether maker and taker notional
  balance (pre-registration, section 11).
- **Published:** cohort cells, market-level proxies and statistics only. No snapshot row, no fill and no address.

### 2. Funding and premium: Ironflow

- **Dataset:** [`ironflow/hyperliquid-funding`](https://huggingface.co/datasets/ironflow/hyperliquid-funding),
  revision `b2153cf3aed73b6b200a378064538a2263923e9d` (`FUNDING_REVISION` in `src/herdbench/sources/ironflow.py`).
  No login and no key.
- **Content:** Hyperliquid's settled funding rates, collected by Ironflow from the venue's public `fundingHistory`
  API; one Parquet file per month (`data/YYYY-MM.parquet`).
- **Taken:** the months July 2025 to August 2026, saved as downloaded (`herdbench fetch --only funding`).
  HerdBench reads four columns, `timestamp`, `market`, `funding_rate` and `premium`; removes the `-PERP` suffix
  from market names so they match snapshot names; and drops duplicate (market, time) rows. The pinned revision
  fixes the content; there is no separate per-file hash check.
- **Check against the venue:** as registered, a sample is checked against Hyperliquid's API. `herdbench check`
  draws one seeded two-day window for each of BTC, ETH, SOL, HYPE and xyz:NVDA, reads the same hours from
  `fundingHistory` and requires funding and premium to agree to 10⁻¹². The outcome is published in
  `results/funding_check.json`; no analysis stage depends on the network.
- **Terms:** CC BY 4.0. Ironflow asks to be credited as "Ironflow (ironflow.sh)". Its dataset card also states
  that files published before 10 November 2026 stay CC BY 4.0 and that new months are CC BY-NC 4.0. Every month
  HerdBench reads was published before that date. A later revision may carry the non-commercial terms, so a change
  to `FUNDING_REVISION` needs a licence check.
- **Published:** the monthly files are not redistributed. The proxies built from them (`funding_24h`,
  `funding_7d`, `pinned_share_24h`, `pinned_share_7d`, `premium_24h`, `abs_premium_24h`) are published in
  `proxies.parquet` with credit to Ironflow, and they enter the statistics.

### 3. Hyperliquid info API (entity map)

- **Endpoint:** `https://api.hyperliquid.xyz/info`. Public; no key and no account.
- **Use:** the entity map, and nothing else in the build (`herdbench build entities`). `userRole` returns an
  address's role (user, agent, vault, subAccount or missing) and, for a sub-account, its master. `subAccounts`
  returns a master's sub-accounts. A master and its sub-accounts form one entity; every other address, and every
  vault, is its own entity.
- **Addresses looked up:** found at the address level, since entities are not known yet. They are every
  market-maker candidate (an address that passes the market-maker tests in any 30-day window with the loosest
  maker share, 70%, and without the turnover test) and every address that is ever one of the three largest
  holders of a market with at least $1M of open interest on the same snapshot ([deviation 3](deviations.md)).
  The list is kept in `derived/entity_map_targets.parquet`.
- **Pacing:** the public limit is 1,200 request weight per minute; `userRole` weighs 60 and `subAccounts` 20.
  Requests are spaced 3.4 s and 1.15 s apart, about 1,050 weight a minute when both run. Each answer is appended
  to `derived/entity_roles.jsonl` as it arrives, so an interrupted run resumes where it stopped. The truth stage
  refuses to run until every address has an answer and every master's sub-account list has been read.
- **Other requests:** the client also reads `fundingHistory` for `herdbench check` (above), and can read
  `candleSnapshot` and `perpDexs`, which no stage calls.
- **Published:** no answer from this API and no part of the map. `results/truth_panel_summary.json` and the
  pre-analysis addendum record only counts (such as addresses looked up and mapped, and the size of the largest
  entity) and the map's SHA-256, taken over the sorted (address, master) pairs in compact JSON. The hash lets a
  rerun confirm that it used the same map without listing any address.

### 4. Binance Vision

- **Archive:** [`data.binance.vision`](https://data.binance.vision), USDT-margined futures (`data/futures/um/`).
  No login and no key.
- **Taken:** three file types for the 30 symbols of the primary panel (`src/herdbench/data/primary_panel.json`),
  saved as downloaded (`herdbench fetch --only binance`). A day or month that Vision never published (HTTP 403 or
  404) is skipped, not treated as an error. Every proxy built from these files has a name starting with `bn_`.

| Vision file | Period | Columns read | Proxies |
|---|---|---|---|
| `daily/metrics/<SYMBOL>/<SYMBOL>-metrics-YYYY-MM-DD.zip` (5-minute rows) | 20 Aug 2025 to 18 Aug 2026 | `create_time`, `sum_open_interest`, `sum_open_interest_value`, `count_long_short_ratio`, `count_toptrader_long_short_ratio`, `sum_toptrader_long_short_ratio`, `sum_taker_long_short_vol_ratio` | `bn_global_ls`, `bn_top_account_ls`, `bn_top_position_ls`, `bn_oi_change_7d` |
| `monthly/klines/<SYMBOL>/5m/<SYMBOL>-5m-YYYY-MM.zip` | Aug 2025 to Aug 2026 | `open_time`, `close_time`, `volume`, `taker_buy_volume` | `bn_taker_ls` |
| `monthly/fundingRate/<SYMBOL>/<SYMBOL>-fundingRate-YYYY-MM.zip` | Aug 2025 to Aug 2026 | `calc_time`, `funding_interval_hours`, `last_funding_rate` | `bn_funding_24h` (secondary) |

`count_long_short_ratio` is Binance's global account long/short ratio, `count_toptrader_long_short_ratio` the
top-trader account ratio and `sum_toptrader_long_short_ratio` the top-trader position ratio; the mapping was
checked against Binance's REST endpoints (`src/herdbench/sources/binance.py`). A Vision row stamped T carries
information as of T + 5 minutes, and a ratio more than 60 minutes old at the cutoff counts as missing.

- **Panel selection:** the primary panel is the 30 largest main-dex markets by open interest on the 27 August 2025
  snapshot, among those with a Vision metrics file for their USDT perpetual on that date. The frozen selection
  ships as `primary_panel.json`.
- **Terms:** Binance's Vision Dataset Terms license the data under CC BY-NC-SA 4.0. Commercial use needs a
  separate licence from Binance, and the terms extend to data derived from or calculated with the datasets. A
  redistributed derivative must credit Binance Vision and stay under CC BY-NC-SA 4.0.
- **Never redistributed:** neither the zip files nor any value parsed from them. The published `proxies.parquet`
  holds no `bn_` column; a test enforces this.
- **Published:** statistics only, all in `results/binance/` under CC BY-NC-SA 4.0. Any statistic that involves a
  Binance proxy goes there. So does the whole primary family (`primary_family.csv`, `primary_family.json`): its
  seven proxies are scored on common market-days and ranked together, so every number in it depends on the
  Binance proxies.

### 5. FINRA short interest

- **Files:** `https://cdn.finra.org/equity/otcmarket/biweekly/shrtYYYYMMDD.csv`, FINRA's twice-monthly equity
  short-interest files (exchange-listed and OTC securities), one per settlement date. No key.
- **Taken:** every file with a settlement date from 1 November 2025 to 31 August 2026
  (`herdbench fetch --only finra`). Settlement dates are found by probing candidate weekdays with HEAD requests;
  the dates found are saved in `raw/finra/settlement_dates.json`, and each file is cached as text. Twelve fields
  are read: symbol, issue name, exchange code, market class, current and previous short position, average daily
  volume, days to cover, revision flag, change percent, change in shares and settlement date.
- **Use:** the stock transfer panel. For each US single-stock perpetual on the `xyz` dex and each settlement date
  from 15 December 2025 to 14 August 2026, the FINRA proxies are `si_change_pct` (change percent),
  `days_to_cover` and `log_short_interest` (log of the current short position). The publication dates used by the
  real-time pairing are fixed in `src/herdbench/grading/transfer.py`. FINRA issue names from the 14 August 2026
  file were also used once to classify the `xyz` markets; the result is a fixed list in `src/herdbench/universe.py`.
- **Terms:** as recorded in `src/herdbench/sources/finra.py`, FINRA's website terms restrict redistribution and
  derived databases, while its API terms allow derived data with FINRA named as owner and source, for
  non-commercial use.
- **Published:** the files are never redistributed, and no per-stock or per-date value is published. Only summary
  statistics of the stock panel (standardised slopes, standard errors, intervals, power, and p-values when power
  allows) appear, in `results/finra/` (`transfer_settlement.csv`, `transfer_publication.csv`, `transfer.json`).
  Each file names FINRA as source and owner and limits use to non-commercial purposes.

### 6. CFTC Commitments of Traders

- **API:** CFTC's public reporting service (Socrata), `https://publicreporting.cftc.gov/resource/`: the
  futures-only Disaggregated report (`72hh-3qpy`) and the futures-only Traders in Financial Futures report
  (`gpe5-46if`). No key.
- **Taken:** reports dated 1 July 2025 to 31 August 2026 for 14 contracts (`herdbench fetch --only cftc`), matched
  to perpetuals in `BRIDGE` (`src/herdbench/sources/cftc.py`) and `EXTRA_COT` (`src/herdbench/fetch.py`).

| Report | Trader group | Contracts |
|---|---|---|
| Disaggregated | Managed money | Gold, silver and copper (COMEX); platinum, palladium, WTI crude oil and natural gas (NYMEX) |
| Traders in Financial Futures | Leveraged funds; asset managers as a second group | E-mini S&P 500, Nasdaq-100 mini, Euro FX, Japanese yen, British pound, Bitcoin, Ether cash-settled (CME) |

Fields read: report date, total open interest, and each group's long and short positions. Net share is
(long - short) / open interest. One Parquet file per contract is stored under `raw/cftc/`; it is a parsed extract,
not the API response.

- **Terms:** US government work, in the public domain.
- **Published:** `results/cot_bridges.csv` (managed money or leveraged funds) and
  `results/cot_bridges_asset_managers.csv`: per market, the number of weeks and the Spearman correlation (with its
  p-value) between the group's net share and ni(non_mm) at the first snapshot after the report date. Description
  only, under CC BY 4.0. The weekly series are not published.

### 7. Coinalyze (optional)

- **API:** `https://api.coinalyze.net/v1/`. Needs a free key in the `COINALYZE_API_KEY` environment variable;
  without one the client raises an explicit error.
- **Use:** optional cross-source checks only. HerdBench never requires it: no build stage calls it, no published
  result depends on it, and nothing from it is published.
- **Key handling:** the service accepts the key only as the `api_key` URL parameter. The client rebuilds every
  error without the URL or the chained exception, and its `repr` omits the key, so no error message or traceback
  contains it (`tests/sources/test_coinalyze.py`). Requests are spaced 1.6 s apart, inside the free tier's 40 calls
  a minute.

### Requests

All requests go through one client (`src/herdbench/_http.py`). It identifies itself as
`herdbench/1.0 (+https://github.com/z33shxn/herdbench)`, retries connection errors, timeouts, HTTP 429 and 5xx
with exponential backoff (honouring `Retry-After`), and spaces requests to stay inside each source's public rate
limit.

## What HerdBench publishes

"Native" means built from Hyperliquid data only: the archive and Ironflow's funding files.

| What | Where | Licence |
|---|---|---|
| Truth panel: cohort cells that pass disclosure control, rounded | `results/truth_panel.parquet`, shipped as `src/herdbench/data/truth_panel.parquet` | CC BY 4.0 |
| Native proxies (no Binance column; `comovement_30d` here is measured against every main-dex market on the date, while the study's tables recompute it against the scored panel) | `results/proxies.parquet`, shipped as `src/herdbench/data/proxies.parquet` | CC BY 4.0 |
| Native statistics (grids, positive control, robustness cohorts, complete-window sensitivity, liquidation target), the CFTC bridges, the gates, the funding cross-check, the truth-panel and score summaries, and the manifest | `results/*.csv`, `results/*.json` | CC BY 4.0 |
| Every statistic that involves Binance Vision data, including the whole primary family | `results/binance/` | CC BY-NC-SA 4.0 |
| Stock transfer statistics from FINRA short interest | `results/finra/` | FINRA's terms: summary statistics only, FINRA named as source and owner, non-commercial use |
| Code | This repository and the `herdbench` package | Apache-2.0 ([`LICENSE`](../LICENSE), [`NOTICE`](../NOTICE)) |

No result is reported on this page; for the published numbers, see [docs/results.md](results.md).

- Every CSV row states its terms: a `licence` column in `results/` and `results/binance/`, and a `source` column
  naming FINRA in `results/finra/`. `primary_family.json`, `liquidation_delta_rho.json` and `transfer.json` carry
  the same notes.
- The split is mechanical (`write_split` in `src/herdbench/pipeline.py`): a statistics row whose proxy name starts
  with `bn_` goes to `results/binance/`, every other row to `results/`. The primary family is written to
  `results/binance/` as a whole. Tests check that no `bn_` row reaches an open-licence file. Native rows on the
  primary panel are scored on the confirmatory family's market-days, so which days they use depends on when Binance
  data exist; no Binance value enters them, and they are published with the native statistics.
- In the repository, the two Parquet files are stored once, in `src/herdbench/data/`, where the package loads them;
  `results/manifest.json` lists their SHA-256 with every other published file. The package's Apache-2.0 licence
  covers its code; the data files bundled with it remain CC BY 4.0.
- `docs/results.md` and the figures in `docs/figures/` are rendered from these files by `herdbench report`. A table
  or figure drawn from `results/binance/` or `results/finra/` carries the terms of that folder.

Attribution lines:

```
Material under CC BY 4.0     HerdBench (Mohammed Zeeshan), https://github.com/z33shxn/herdbench
Funding and premium inputs   Ironflow (ironflow.sh), hyperliquid-funding dataset, CC BY 4.0
results/binance/             Contains statistics derived from Binance Vision data (https://data.binance.vision),
                             licensed CC BY-NC-SA 4.0; shared under the same licence.
results/finra/               Source and owner of the short-interest data: FINRA. Summary statistics only;
                             non-commercial use.
```

**Provenance.** The mirror and Ironflow are read at fixed commit hashes. Vision, FINRA and CFTC data have no
revision to pin: they are addressed by symbol, contract and date, and a rerun reads whatever the source serves at
the time. `results/manifest.json` records the HerdBench version, the mirror repository and revision, the Ironflow
dataset and revision, the SHA-256 of the pre-registration and of the pre-analysis addendum, and the SHA-256 of
every published file.

## Privacy and disclosure control

Disclosure control applies to publication only. The pre-registered statistics use every eligible cell, suppressed
or not; the one exception is the rerun of the primary family on the published panel, which is labelled
"in-sample, published panel". The rules are in [`src/herdbench/truth/privacy.py`](../src/herdbench/truth/privacy.py),
with parameters in `PREREG` (pre-registration, section 12). A cell is one market, one snapshot date and one
cohort. Every count is of entities: a master account and its sub-accounts count once.

1. **No address and no entity-level row.** Published files hold cohort cells, market-level proxies and statistics
   only. The entity map is published only as counts and a hash. The only addresses anywhere in the repository are
   the protocol accounts that `PREREG` removes from every cohort (the HLP vault, its leader and child accounts,
   and the builder-dex backstop liquidators).
2. **Cell rule.** A cell is published only if at least 20 entities contribute to it (`min_entities`) and its two
   largest entities hold at most 80% of its gross notional (`dominance_top2_max`).
3. **Complement rule.** Cohorts are nested on each market-day: `all` contains `non_mm`, and `non_mm` contains
   `top_av`, `trend` and `contrarian`. For each nested pair, the entities in the larger cell but not in the smaller
   one must pass the cell rule; if they fail, the larger cell is suppressed. No cell can then be recovered by
   subtracting one published cell from another.
4. **Rounding.** Ratios (`ni`, `ai`, `lwi`, `abs_ni`, `top10_share`, `fragile_share`, `coheld_share`, `dtl`) to 3
   decimals; notional (`gross_notional`, `net_notional`, `oi_notional`) to 2 significant figures; entity counts
   down to a multiple of 10. The columns the rules use (`top2_share`, `suppressed`) are dropped.
5. **Robustness cohorts are never published as cells.** The four analysis-only cohorts (`non_mm_s070`,
   `non_mm_s090`, `non_mm_t020`, `non_mm_t100`) are always suppressed. Only their statistics appear, in
   `robustness_cohorts.csv` (in `results/` and `results/binance/`).

The share of primary market-days suppressed is recorded in `results/truth_panel_summary.json`; above 5%
(`suppressed_flag_share`), the published-panel rerun is flagged "not representative" (see
[docs/results.md](results.md)). `herdbench.grade()` scores only against the published panel and labels every
result "in-sample, published panel".

| Test | What it checks |
|---|---|
| `tests/truth/test_metrics_privacy.py` | Cell-rule boundaries (19 against 20 entities; 80% against 81%); the complement rule suppresses the larger cell only when the difference fails; rounding; suppressed cells and internal columns are dropped |
| `tests/truth/test_build.py` | The panel built from a synthetic archive is rounded and holds no address and no small cell |
| `tests/test_pipeline.py` | End to end on a synthetic archive: no result file (CSV, JSON, Markdown or Parquet) contains an address; no `bn_` row reaches an open-licence file; the addendum records the map hash without any address |
| `tests/test_bundled.py` | The shipped panel passes disclosure control; the shipped proxies hold no `bn_` column; no Markdown, results or source file names an address outside the protocol list; every published file matches its hash in the manifest |

## On-disk layout

`herdbench fetch` downloads every input into `raw/` (about 25 GB). `herdbench build` writes private intermediate
tables to `derived/` and published files to the results folder. `herdbench doctor` reports which inputs are
present. Full steps: [docs/reproduce.md](reproduce.md).

- **Data folder:** `--data`, else `HERDBENCH_DATA_DIR`, else `data/` in the current directory.
- **Results folder:** `--results`; else `<data folder>/results` when `--data` is given; else
  `HERDBENCH_RESULTS_DIR`; else `results/` in the current directory.

```
data/
  raw/
    hf/
      perp_archive/date=YYYY-MM-DD/<block>_<ms>.parquet     snapshots, verbatim (addresses)
      fills_slim/fills_YYYY-MM-DD.parquet                   fills, 14 columns (addresses)
      fills_slim/fills_YYYY-MM-DD.json                      provenance sidecar
    ironflow/hyperliquid-funding/YYYY-MM.parquet            funding and premium, as downloaded
    binance/futures/um/
      daily/metrics/<SYMBOL>/<SYMBOL>-metrics-YYYY-MM-DD.zip
      monthly/klines/<SYMBOL>/5m/<SYMBOL>-5m-YYYY-MM.zip
      monthly/fundingRate/<SYMBOL>/<SYMBOL>-fundingRate-YYYY-MM.zip
    finra/
      shrtYYYYMMDD.csv                                      one file per settlement date
      settlement_dates.json                                 settlement dates found
    cftc/<contract code>.parquet                            parsed series, one file per contract
  derived/                                                  see the table below
```

| `derived/` path | Content | Holds addresses |
|---|---|---|
| `fills_daily/activity_YYYY-MM-DD.parquet` | Maker and taker notional per address and day | Yes |
| `fills_daily/{volume,liquidations,hourly,liq_hourly}_YYYY-MM-DD.parquet` | Volume, liquidations and hourly bars per market | No |
| `markets.parquet`, `markets.json` | Mark, open interest, long-short gap and capture time per snapshot and market; the snapshot files used | No |
| `style/contrib_YYYY-MM-DD.parquet` | Trend-score contributions per address | Yes |
| `entity_map_targets.parquet` | Addresses looked up for the entity map | Yes |
| `entity_roles.jsonl` | `userRole` and `subAccounts` answers: the entity map | Yes |
| `entity_gross.parquet`, `entity_gross.json` | Gross notional per entity and snapshot; entities are keyed by address | Yes |
| `mm_sets.json` | Market-maker entities per rule and date, keyed by address | Yes |
| `truth_cells.parquet` | Every cell, including suppressed cells and robustness cohorts, unrounded, with `top2_share` | No, but it must stay private |
| `truth_info.json` | Entity-map counts and hash; market-maker set sizes | No |
| `proxies.parquet` | Every proxy, Binance proxies included | No (Binance terms apply) |
| `binance/{metrics,klines,funding}.parquet` and `.json` | Parsed Binance Vision data and its cache keys | No (Binance terms apply) |
| `reconciliation_pairs.json` | Reconciliation totals per snapshot pair | No |
| `primary_panel.json` (optional) | A panel selection from `herdbench build panel-selection`; overrides the bundled copy | No |

**`derived/` must never be shared.** It holds addresses, the entity map, every cell before suppression and
rounding, and values parsed from Binance Vision. Releasing any of it would undo disclosure control or breach
Binance's terms. Do not share `raw/` either: it holds third-party files that HerdBench does not redistribute,
including the archive's address-level snapshots and fills. The repository's `.gitignore` excludes `/data/` and
`/derived/` at its root.

`herdbench fetch` is the only command that downloads input files. Two build stages query a source directly:
`herdbench build entities` (the info API; resumable, and it takes hours) and `herdbench build panel-selection`
(which checks the Vision metrics files for the selection date). Every other stage reads local files only and
stops with an error when an input is missing.
