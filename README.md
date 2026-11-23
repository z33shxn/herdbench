# HerdBench

**Do public crowding signals measure what traders actually hold?**

HerdBench grades the signals desks use to infer positioning (funding, premium, open interest, momentum,
comovement, liquidations, exchange long/short ratios and equity short interest) against the *observed* positions of
every account on Hyperliquid, a derivatives venue where all open positions are public. The design was
pre-registered and timestamped before any signal was compared with the truth. The code, the published truth
panel, every result and the means to reproduce them are in this repository.

[![CI](https://github.com/z33shxn/herdbench/actions/workflows/ci.yml/badge.svg)](https://github.com/z33shxn/herdbench/actions/workflows/ci.yml)
![Python 3.10–3.13](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)
[![Code: Apache-2.0](https://img.shields.io/badge/code-Apache--2.0-blue)](LICENSE)
![Results: CC BY 4.0](https://img.shields.io/badge/results-CC%20BY%204.0-lightgrey)
[![Pre-registered](https://img.shields.io/badge/pre--registered-18%20Nov%202026-success)](docs/preregistration.md)

<!-- RESULTS:HEADLINE -->

## Why a benchmark like this did not exist

Positioning is almost never observed. Funding rates, basis, open-interest changes and exchange long/short ratios
are read as crowding signals because the books behind them are private. On Hyperliquid every account's open
position is published, so for once the truth can be measured for the whole market, every day, and the signals can
be graded against it.

HerdBench measures **net positioning of the crowd**: every long has a short, so all accounts together net to zero;
the informative quantity is what remains after removing market makers and protocol accounts,
`ni(non_mm) = −(market-maker net + protocol net) ÷ non_mm gross`. Sub-accounts are merged into their master
account first, so one firm counts once.

## Use it in two minutes

```bash
pip install "herdbench @ git+https://github.com/z33shxn/herdbench"
herdbench info
```

```python
import herdbench

panel = herdbench.load_truth_panel(cohort="non_mm")   # date x market truth: ni, ai, lwi, crowding metrics
report = herdbench.grade(my_signal)                   # your signal vs ni(non_mm), with the study's baselines
print(report)
```

The panel ships with the package (no download, no account). `herdbench panel --out panel.parquet` exports it.

## Grade your own signal

`grade()` takes a table with `date`, `market` and one value column (pandas or polars), a pandas Series indexed by
`(date, market)`, or a wide frame (dates × markets). It returns the within-market Spearman correlation averaged on
Fisher's z scale with a block-bootstrap interval, plus the cross-sectional correlation, daily changes, a placebo
(truth shifted 60 days), the two-way fixed-effects slope, sign agreement and tail lift, for your signal and for the
study's own Hyperliquid-native baselines **on exactly the same market-days**.

```bash
herdbench grade my_signal.csv --truth ni --cohort non_mm --json report.json
```

Two rules keep the comparison honest:

1. **Timing.** A value for snapshot date D may use only information from before that snapshot's `cutoff` (a column
   of the panel; usually D 00:00 UTC). Plain dates are read as snapshot dates; timestamped values are moved to the
   first snapshot whose cutoff is at or after them, so nothing is scored against a snapshot taken before the value
   was known. The study's own proxies obey the same rule.
2. **Labelling.** Results are labelled *in-sample, published panel*: the panel contains only the cells that pass
   disclosure control, rounded. The pre-registered headline results were computed on every cell and ship as fixed
   tables in [`results/`](results/).

Truth metrics you can grade against: `ni`, `ai` (account imbalance), `lwi` (leverage-weighted imbalance) and the
crowding metrics `abs_ni`, `top10_share`, `fragile_share`, `coheld_share`, `dtl` (days to liquidate). Cohorts: `all`,
`non_mm`, `top_av` (largest 20% by lagged account value), `trend`, `contrarian`. Definitions:
[docs/methodology.md](docs/methodology.md).

## What is in the truth panel

One row per snapshot date, market and cohort, for every cell that passes disclosure control.

| Column | Meaning |
|---|---|
| `date`, `cutoff` | Snapshot date and the proxy cutoff (last full UTC hour before the snapshot was captured) |
| `market`, `dex` | Market name and venue (`main` or a builder-deployed dex such as `xyz`) |
| `cohort` | `all`, `non_mm`, `top_av`, `trend` or `contrarian` |
| `n_entities` | Entities in the cell, rounded down to a multiple of 10 |
| `gross_notional`, `net_notional` | Cohort gross and net position notional (2 significant figures) |
| `ni`, `ai`, `lwi` | Net, account and leverage-weighted imbalance (3 decimals) |
| `abs_ni`, `top10_share`, `fragile_share`, `coheld_share` | Crowding metrics (3 decimals) |
| `dtl` | Days to liquidate: \|cohort net notional\| ÷ mean daily taker volume over the prior 7 days |
| `oi_notional`, `eligible` | Market open interest; eligible if open interest at the previous snapshot was at least $1M |

No address and no entity-level row is ever published. A cell is published only with at least 20 entities and
its two largest entities holding at most 80% of its gross notional; a complement rule stops any cell being
recovered by subtracting two published ones ([docs/data.md](docs/data.md)).

## How the study works

```
Hyperliquid archive (daily all-account snapshots + every fill)      public proxies, each computed before the cutoff
        |                                                                      |
  entities (master + sub-accounts) -> market makers removed -> cohorts         |
        |                                                                      |
  truth panel (ni, ai, lwi, crowding)  ------------- graded against ---------- +
        |
  primary family (7 proxies, Holm) . secondary grids (BH) . liquidations . US stocks (FINRA) . CFTC bridges
```

* **Truth** comes from daily snapshots of every open position, reconciled against every fill between snapshots.
* **Proxies** use only data stamped before each snapshot's cutoff, so none can see the positions it is scored on.
* **Statistics** are fixed in the [pre-registration](docs/preregistration.md): within-market Spearman correlations
  averaged on the z scale, a circular block bootstrap with shared draws, a rotation null, a two-way fixed-effects
  slope, Holm correction for the confirmatory family and four verdicts (confirmed, reversed, uninformative,
  inconclusive).

Full method: [docs/methodology.md](docs/methodology.md). Results with every table and figure:
[docs/results.md](docs/results.md).

## Integrity

* **Pre-registered and timestamped.** Every parameter is in [docs/preregistration.md](docs/preregistration.md) and
  in code (`herdbench.config.PREREG`); a test fails if the two disagree. The registering commit and the pre-analysis
  addendum are anchored with OpenTimestamps proofs in [docs/timestamps/](docs/timestamps/). Scoring refuses to run
  without the addendum.
* **Gated.** Every result depends on two gates computed on the full archive: snapshots must reconcile with fills, and
  maker notional must equal taker notional.
* **Reproducible.** `herdbench fetch`, `herdbench build entities` and `herdbench build all` rebuild every number
  from public data ([docs/reproduce.md](docs/reproduce.md)); `herdbench check` cross-checks the funding inputs against
  the venue's own API; [`results/manifest.json`](results/manifest.json) holds the SHA-256 of
  every published file. The test suite builds a complete synthetic archive and runs the whole pipeline offline on
  Linux, Windows and macOS, Python 3.10 to 3.13.
* **Deviations are reported**, with the registered version still run: [docs/results.md](docs/results.md#10-deviations-from-the-pre-registration).

## Licences

| What | Licence |
|---|---|
| Code | Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)) |
| Truth panel, native proxies and statistics in `results/` | CC BY 4.0 |
| Statistics involving Binance data, in `results/binance/` (including the primary family) | CC BY-NC-SA 4.0 |
| Statistics from FINRA short interest, in `results/finra/` | FINRA's terms (source and owner FINRA; non-commercial) |

Raw inputs are never redistributed. Details: [docs/data.md](docs/data.md).

## Limitations

* One venue and one year (28 August 2025 to 18 August 2026). Hyperliquid's crowd is not a sample of equity funds.
* The books are complete on this venue but not across venues: the same traders may hold offsetting positions
  elsewhere.
* The study measures agreement between signals and positioning. It makes no causal claim and estimates no
  crowding premium.
* Entities are merged through the venue's own sub-account links for market-maker candidates and top holders; other
  addresses count as their own entity, so a firm using unlinked wallets can count more than once.

## Citation

See [CITATION.cff](CITATION.cff). Author: Mohammed Zeeshan.
