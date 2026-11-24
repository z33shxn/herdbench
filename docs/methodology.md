# Methodology

How HerdBench measures positioning on Hyperliquid and grades public proxies against it.

This document states what the code does, in the order the pipeline does it, for a researcher deciding whether to
trust the numbers. Each section links to the matching section of the [pre-registration](preregistration.md) and
names the code that implements it. Code paths are relative to `src/herdbench/`; `results/` and `docs/` paths are
relative to the repository root.

Every registered parameter quoted here is a field of `herdbench.config.PREREG`, and `tests/test_preregistration.py`
fails if the pre-registration and `PREREG` state different values. Some smaller constants (tolerances, minimum
counts, seed offsets) are fixed in the code but are not `PREREG` fields, so that test does not cover them. They are
named where they apply and collected in [Constants set in code](#23-constants-set-in-code). Where the code differs from
the registered wording, the section says so. Departures recorded by the author are listed in
[deviations.md](deviations.md). No result is reported here; for results, see [docs/results.md](results.md).

Three controls surround the analysis:

1. **Registration.** The design was registered on 18 November 2026, before any proxy was compared with the truth. A
   timestamp proof of the registering commit is in `docs/timestamps/`.
2. **Pre-analysis addendum.** After the entity map and the truth panel are built, and before any proxy is compared
   with the truth, `herdbench build addendum` writes `docs/preregistration_addendum.md`. It records the entity map's
   SHA-256, the market-maker counts, and the coverage and suppression of the truth panel. The protocol commits and
   timestamps it before scoring, and `herdbench build score` refuses to run without it.
3. **Reproducibility.** `herdbench fetch` and the `herdbench build` stages rebuild every published number from public
   data. The archive and the funding files are read at fixed revisions; Binance, FINRA and CFTC files are read as
   their sources serve them. Every resampling step is seeded, and `results/manifest.json` records the SHA-256 of
   every published file.

Notation used throughout:

```
D        a snapshot date (UTC)
c        the cutoff of the snapshot dated D (section 2.5)
[a, b)   a window that includes a and excludes b
L, S     long and short notional of a cell, in USD
P(t)     the last trade price before t (section 8)
OI(D)    open interest in contracts at the snapshot dated D (section 8)
```

The pipeline (`pipeline.py`) runs in cached stages. Each stage reads the previous stage's output and can be rerun
alone.

| Stage | Command | Output |
|---|---|---|
| inputs | `herdbench fetch` | raw downloads; never published |
| panel selection | `herdbench build panel-selection` | the primary panel; a frozen copy ships as `data/primary_panel.json` |
| entities | `herdbench build entities` | the entity map; never published |
| truth | `herdbench build truth` | every cohort cell with its disclosure flags; never published |
| proxies | `herdbench build proxies` | every proxy for every main-dex and `xyz` market-day of the evaluation window |
| publish | `herdbench build publish` | the public truth panel, the native proxies and a coverage summary |
| addendum | `herdbench build addendum` | `docs/preregistration_addendum.md`, to be committed and timestamped before scoring |
| score | `herdbench build score` | every statistic |
| gates | `herdbench build gates` | `results/gates.json` |
| manifest | `herdbench build manifest` | `results/manifest.json` |

`herdbench build all` runs truth, proxies, publish, score, gates and manifest. The truth stage stops unless the
entity map is complete, and the score stage stops unless the addendum exists.

Contents:

1. [The venue and why positions are observable](#1-the-venue-and-why-positions-are-observable)
2. [Data](#2-data)
3. [Entities](#3-entities)
4. [Protocol accounts](#4-protocol-accounts)
5. [The market-maker rule](#5-the-market-maker-rule)
6. [Cohorts](#6-cohorts)
7. [Truth metrics](#7-truth-metrics)
8. [Proxies](#8-proxies)
9. [Eligibility and the scored panels](#9-eligibility-and-the-scored-panels)
10. [The primary statistic](#10-the-primary-statistic)
11. [The bootstrap](#11-the-bootstrap)
12. [The rotation null](#12-the-rotation-null)
13. [The fixed-effects slope](#13-the-fixed-effects-slope)
14. [The four verdicts and Holm](#14-the-four-verdicts-and-holm)
15. [Ranks](#15-ranks)
16. [Stability and kill criterion 4](#16-stability-and-kill-criterion-4)
17. [Secondary statistics and Benjamini-Hochberg](#17-secondary-statistics-and-benjamini-hochberg)
18. [Target (b): later liquidations](#18-target-b-later-liquidations)
19. [Transfer to US single-stock perpetuals](#19-transfer-to-us-single-stock-perpetuals)
20. [CFTC futures bridges](#20-cftc-futures-bridges)
21. [Gates and kill criterion 1](#21-gates-and-kill-criterion-1)
22. [Disclosure control](#22-disclosure-control)
23. [Constants set in code](#23-constants-set-in-code)

## 1. The venue and why positions are observable

Registered in [§1 Question and claims](preregistration.md#1-question-and-claims). Code: `truth/snapshots.py`,
`universe.py`.

Hyperliquid is a derivatives venue whose state is public. Anyone can read every account's open perpetual
positions, with size, USD notional, liquidation price and account value, and every fill. The archive HerdBench reads
republishes that public data ([data.md](data.md)). HerdBench therefore observes the positioning of every account on
the venue instead of inferring it from aggregates.

Markets sit on the main dex (crypto perpetuals named by coin, such as `BTC`) or on builder-deployed dexes (named
`dex:TICKER`, such as `xyz:NVDA`). The `xyz` dex lists US stocks, ETFs, ADRs, a preferred share, indices,
commodities, currencies, and non-US and unlisted names; `universe.py` classifies its markets. Truth cells are built
for every market on every dex. Proxies are computed for main-dex and `xyz` markets.

Every long has a short. In each market the signed notional of all accounts sums to zero, so the net position of
all accounts carries no information. The informative quantity is the net position of a sub-population. With
protocol accounts (section 4) and market makers (section 5) separated out:

```
non_mm net + market-maker net + protocol net = 0

ni(non_mm) = non_mm net ÷ non_mm gross = -(market-maker net + protocol net) ÷ non_mm gross
```

ni(non_mm) is the primary truth, called net positioning. "Crowding" refers to the unsigned truths of section 7.
For each snapshot and market the pipeline records how far the books are from the identity, as `long_short_gap`
(section 2.2).

The study measures agreement between proxies and observed positioning. It makes no causal claim, estimates no
crowding premium or capacity curve, does not treat these accounts as a sample of equity funds, and does not assume
the books are complete across venues: the same trader may hold offsetting positions elsewhere.

## 2. Data

Registered in [§2 Pre-analysis record](preregistration.md#2-pre-analysis-record-everything-seen-before-registration)
and [§3 Data](preregistration.md#3-data); the cutoff rule in
[§7 Proxies, timing and the primary panel](preregistration.md#7-proxies-timing-and-the-primary-panel). Code:
`fetch.py`, `sources/`, `truth/snapshots.py`, `truth/market_aggregates.py`, `truth/build.py`. Terms, fetching and the
on-disk layout are in [data.md](data.md).

### 2.1 Sources

| Input | Source | Version control | Used for |
|---|---|---|---|
| All-account position snapshots | Hugging Face dataset `craftify2221/hyperliquid-fills-raw` | commit `PREREG.mirror_revision`; each file is checked against the SHA-256 the Hub publishes | truth, open interest, marks, style scores, reconciliation |
| Every fill, one file per UTC date | the same dataset | the same commit; each file's published SHA-256 is recorded (the file is read in parts, so the hash cannot be checked) | market-maker rule, prices, volume, liquidations, gates |
| Hourly settled funding rate and premium | Ironflow `hyperliquid-funding` (CC BY 4.0), a copy of the venue's `fundingHistory` | `ironflow.FUNDING_REVISION` | funding and premium proxies |
| Long/short ratios, open interest, 5-minute klines, funding | Binance Vision, USDT-margined futures | none: files are addressed by symbol and date | `bn_*` proxies, primary panel only |
| Equity short interest | FINRA twice-monthly files | none: files are addressed by settlement date | stock transfer |
| Commitments of Traders | CFTC public reporting API, futures-only reports | none: series are addressed by contract code and report date | futures bridges |
| Address roles and sub-account lists | Hyperliquid info API (`userRole`, `subAccounts`) | answers cached locally; the resulting map is hashed in the addendum | entity map |

`herdbench fetch` downloads the market data. Two build stages query a source directly: `herdbench build entities`
(the info API) and `herdbench build panel-selection` (Vision's metrics files for the selection date). Every other
stage reads local files only and stops with an error when an input is missing. Raw inputs are never redistributed.
Statistics go to `results/` (built from Hyperliquid, Ironflow and CFTC data; CC BY 4.0), `results/binance/`
(anything that uses Binance data, including the primary family; CC BY-NC-SA 4.0) and `results/finra/` (built from
FINRA data; FINRA's terms).

### 2.2 Position snapshots

A snapshot file is `perp_archive/date=YYYY-MM-DD/<block>_<capture ms>.parquet`; the capture time, in Unix
milliseconds (UTC), comes from the file name. HerdBench uses one snapshot per UTC date. When a date has two files
(the pre-registration records one such date, 2026-03-24), the earlier capture is used
(`truth/snapshots.py::local_index`).

Each row is one address's position in one market. HerdBench reads `user`, `market`, `size` (signed; positive is
long), `notional` (absolute USD value), `liquidation_price`, `leverage` and `account_value`, drops rows with zero
size and lower-cases addresses. No metric uses the `leverage` column; the leverage weight of section 7 is computed
from notional and account value.

For each snapshot and market, over all accounts including protocol accounts (`market_table`):

```
mark             = (long notional + short notional) ÷ Σ |size|
oi_notional      = long notional
oi_contracts     = Σ size of long positions
long_short_gap   = (long notional - short notional) ÷ (long notional + short notional)
```

The evaluation window is the snapshots dated 2025-08-28 to 2026-08-18 (`pipeline.eval_dates`). Truth cells, proxies
and statistics are computed for evaluation dates; earlier snapshots and fills feed trailing windows. Three steps
reach outside the evaluation window: the entity-map lookup (section 3), the forward windows of target (b), whose
outcomes run up to 7 days past the last evaluation snapshot (section 18), and the gates (section 21).

Nothing is differenced across a missing date. Style flows need snapshots on three consecutive dates; open-interest
changes use exactly the snapshots dated D - 1 and D - 8; eligibility uses the snapshot dated D - 1; the
reconciliation gate pairs consecutive dates only; the daily-change statistic differences consecutive dates only.

### 2.3 Fills

Fill files (`fills_raw_YYYY-MM-DD.parquet`) are read column by column with HTTP range requests. Fourteen columns
are kept: `coin`, `dex`, `asset_class`, `price`, `size`, `side`, `timestamp`, `direction`, `address`, `crossed`,
`twap_id`, `is_liquidation`, `liquidation_mark_px`, `liquidation_method`. Decimal columns are cast to float64. A
sidecar records the source file's SHA-256 and the mirror revision.

Only perpetual fills (`asset_class` = `perp`) are used. A trade appears once per counterparty: one taker row
(`crossed` true) and one maker row. The notional of a fill is price × size. One pass over every fill day builds
these tables (`truth/build.py::summarize_fill_days`):

| Table | Content | Used by |
|---|---|---|
| activity | maker and taker notional per address and day | market-maker rule |
| volume | taker notional and maker notional per market and day | Gate 0, kill criterion 1 |
| hourly bars | open, high, low, close and taker notional per market and UTC hour, from taker rows only | prices, momentum, volatility, volume, comovement |
| liquidations | liquidated notional per market and hour (and per day), split into longs and shorts | liquidation proxy, target (b) |

An hourly bar exists only for an hour with at least one taker fill; its close is the hour's last taker trade price.
The archive sets `is_liquidation` only on the liquidated account's own fills, for market and backstop liquidations
alike, so summing those fills counts each liquidation once. A liquidated long is closed by a sell, a liquidated
short by a buy.

### 2.4 Funding and premium

Funding and premium come from Ironflow's copy of the venue's settled hourly funding history: one row per market and
hour, with the funding rate and the premium. The `-PERP` suffix is removed from market names so they match the
snapshots, and duplicate (market, time) rows are dropped. As registered, a sample is checked against the venue's
own API: `herdbench check` (`pipeline.check_funding`) draws one seeded two-day window for each of BTC, ETH, SOL,
HYPE and xyz:NVDA, reads the same hours from `fundingHistory` and requires funding and premium to agree to 10⁻¹²;
the outcome is published in `results/funding_check.json`. On this venue the basis equals the premium, so basis is
scored once, as the premium.

### 2.5 Capture times and the cutoff rule

The cutoff c of a snapshot is its capture time truncated to the hour: the last full UTC hour at or before the
capture (`proxies/native.py::cutoff`). Every proxy for that snapshot is built from these inputs only:

- funding and premium rows stamped in [c - 24h, c) or [c - 7d, c);
- prices, volume and liquidations from hourly bars that end at or before c;
- open interest from the snapshots dated D - 1 and D - 8, never the snapshot being scored. These snapshots are
  chosen by date; their capture times are not compared with c;
- Binance metrics rows stamped before c (their information runs up to c; section 8), and Binance klines and
  funding settlements stamped in [c - 24h, c).

The pre-registration's record of capture times: 271 snapshots were captured between 00:00 and 01:00 UTC on their
date, so c is 00:00 on D. Sixty-two were captured later on their date, mostly at about 20:05 UTC from 2025-08-28 to
2025-12-16; for those, c is 20:00 on D. One, dated 2026-04-24, was captured three seconds before midnight, so c is
23:00 UTC on the evening before its date. The rule needs no special case: c is never after the capture.

The cutoff is published in every row of the truth panel (column `cutoff`), so outside signals can follow the same
rule. The settlement-date pairing of the stock transfer is the one registered exemption (section 19).

## 3. Entities

Registered in [§4 Entities and protocol accounts](preregistration.md#4-entities-and-protocol-accounts). Code:
`truth/entities.py`, `truth/build.py::entity_map_targets`, `pipeline.py::stage_entities`. Deviation 3 applies.

A master account can run several sub-accounts, so the unit of analysis is the entity: a master and all of its
sub-accounts. Counting addresses would let one firm pass the 20-entity disclosure rule alone, and would let a market
maker's hedging sub-account sit inside the crowd.

**Which addresses are looked up, and why.** Two groups. Market-maker candidates are looked up so that a market
maker's sub-accounts are merged before the market-maker rule runs. The largest holders are looked up because they
drive the concentration metrics and the dominance part of the disclosure rule. Entities are not known yet, so both
groups are found at the address level, over every snapshot in the archive and every dex:

1. Every market-maker candidate: an address that passes the loosest maker-share rule of section 5 (share 0.70, the
   lowest of the main and robustness shares) together with the $10,000,000 size test and the 10-active-day test, in
   any 30-day window that ends the day before a snapshot date and holds at least 15 fill days. The turnover test is
   not applied.
2. Every address that is ever one of the three largest non-protocol holders, by position notional, of a market with
   at least $1,000,000 of open interest on that same snapshot. The registered text says "eligible market" (open
   interest of at least $1,000,000 at the previous snapshot). Deviation 3 records the difference: the two lists
   differ only on days when a market crosses $1,000,000, and the worst case is an entity counted as two in such a
   market.

**How they are mapped.** For each target, the public `userRole` request returns its role (user, agent, vault,
subAccount or missing) and, for a sub-account, its master. The `subAccounts` request then lists the sub-accounts of
every such master and of every target whose role is user. Each sub-account found, and each of its siblings, is
mapped to its master (`entities.build_map`). Every other address is its own entity, and so is every vault. The truth
stage refuses to run until every target has an answer and every master's list has been read.

**Netting.** Within a market, an entity's address-level positions are netted to one signed position. An entity whose
positions net to zero (|net| at most 10⁻⁹ of its address-level gross) holds nothing in that market. The market-maker
rule, the cohorts, the disclosure rules and every truth metric are computed on entities. Netting leaves net notional
unchanged and reduces gross notional where sub-accounts offset.

**Freezing.** The map's SHA-256 (of the sorted address-master pairs in compact JSON, `entities.map_hash`) is recorded
in the addendum before any proxy is compared with the truth. `herdbench build score --strict` refuses to run unless
the current map hashes to that value (`herdbench build all --strict` does the same). Without `--strict`, scoring
checks only that the addendum exists; a rerun months later may find new sub-accounts and so a different map.
Neither the map nor any address is published.

**Limit.** An address outside both groups is not looked up. A firm that trades through unlinked wallets, or through
sub-accounts whose master and siblings are all outside both groups, counts as several entities.

## 4. Protocol accounts

Registered in [§4 Entities and protocol accounts](preregistration.md#4-entities-and-protocol-accounts). Code:
`config.py::PreRegistration.is_protocol`, `truth/snapshots.py::protocol_expr`.

Two sets of accounts act for the venue rather than for a trader. They are removed from every cohort before entities
are attached:

- the venue's protocol vault (HLP), its leader account and its child accounts: the nine addresses in
  `PREREG.protocol_addresses`;
- the backstop liquidators of builder-deployed dexes: a fixed base address plus the dex index, for offsets 0 to 4095
  (`PREREG.backstop_liquidator_base`, `PREREG.backstop_liquidator_span`).

They stay in open interest, marks and the reconciliation gate, which cover every account. Because they are removed
from `all`, ni(all) is not zero. It is the protocol accounts' book, negated:

```
ni(all) = -protocol net ÷ all gross
```

ai(all) counts entities rather than notional, so it barely depends on where the market-maker line falls. As
registered, it is printed next to every primary row: `primary_family.csv` carries each confirmatory proxy's
statistic against ai(all) on the family's common support (`ai_all_rho`, `ai_all_ci_low`, `ai_all_ci_high`,
`ai_all_p`). Every directional proxy is also scored against ai(all) in the directional grids (section 17).

## 5. The market-maker rule

Registered in [§5 Who counts as the crowd](preregistration.md#5-who-counts-as-the-crowd). Code: `truth/mm.py`,
`truth/build.py::market_maker_sets`.

An entity is a market maker for snapshot date D if it passes all four tests over the 30 calendar days D - 30 to
D - 1, with fills from every perpetual market on every dex pooled. Fills dated D are never used.

| Test | Rule | Parameter |
|---|---|---|
| Passive share | maker notional ÷ (maker + taker notional) ≥ 0.80 | `mm_maker_share` |
| Size | maker + taker notional ≥ $10,000,000 | `mm_min_notional` |
| Activity | at least one perpetual fill on ≥ 10 distinct days | `mm_min_active_days` |
| Turnover | maker + taker notional ≥ 50 × max(mean gross position notional, $1) | `mm_turnover_multiple` |

The mean gross position notional is the average, over the snapshot dates in the window, of the entity's gross
position notional: the sum of the absolute notional of every position of every address of the entity, in every
market, counting zero on a date it holds nothing. If the window holds no snapshot, the denominator is the $1 floor.

The turnover test separates two kinds of mostly passive traders. Market makers turn their inventory over many times
a day. Patient limit-order holders and carry traders, also mostly passive, turn it over far less.

**Coverage and carry-forward.** The status for D is known only if the window holds at least 15 dates with an
archived fill file. Otherwise the most recent known set from an earlier evaluation date at most 15 days before D is
used; the rule is evaluated on evaluation dates only, so warm-up dates supply no set. If there is none, the date has
no non_mm cohort: `non_mm` and every cohort nested in it are empty that day, and `all` is still built.

**Robustness rules.** Four variants change one threshold each and carry forward on their own: maker share 0.70 or
0.90 (turnover 50), and turnover multiple 20 or 100 (share 0.80). They define the analysis-only cohorts of
section 6.

Two shortcuts in the code do not change the result. Entities that never trade $10,000,000 over the whole archive are
dropped before any window is evaluated, since they cannot pass the size test. Turnover denominators are computed only
for entities that pass the share, size and activity tests at the loosest share on some evaluation date, since no
other entity reaches the turnover test under any rule.

## 6. Cohorts

Registered in [§5 Who counts as the crowd](preregistration.md#5-who-counts-as-the-crowd). Code:
`truth/cohorts.py`, `truth/style.py`, `truth/build.py::lagged_account_values`.

On each date, a cohort in a market is a set of entities that hold a nonzero net position in that market. Protocol
accounts are never included.

| Cohort | Members |
|---|---|
| `all` | every non-protocol entity |
| `non_mm` | `all` minus the market makers of section 5; empty when the set is unknown |
| `top_av` | inside `non_mm`: the top 20% of the market's rankable holders by lagged entity account value, k = ceil(0.20 × number rankable), ties broken by entity identifier |
| `trend` | inside `non_mm`: trend score ≥ +0.20 |
| `contrarian` | inside `non_mm`: trend score ≤ -0.20 |

Because `top_av`, `trend` and `contrarian` are drawn from inside `non_mm`, no sub-cohort contains market-maker
inventory.

**Lagged account value.** An entity's account value on a snapshot is, for each of its addresses, the largest account
value reported across that address's position rows, summed over the entity's addresses. (An address can report on
several dexes; taking the largest avoids counting it twice.) `top_av` ranks on the value from the snapshot dated
D - 8 or, if that date is missing, the most recent snapshot dated D - 9 to D - 14. An entity with no value there,
which includes an entity with no open position on that snapshot, is not rankable. If no snapshot exists in that
range, `top_av` is empty that day. The lag keeps a rally from promoting longs into `top_av` through unrealised
profit.

**Trend score.** For each date t with snapshots on t - 2, t - 1 and t (consecutive calendar days), every
non-protocol address and market with a change in size, including a position opened or closed, gives a flow f. The
flow is paired with the market's previous snapshot-to-snapshot log return r (markets quoted on all three snapshots):

```
f = (size_t - size_{t-1}) × mark_t
r = ln(mark_{t-1} ÷ mark_{t-2})
u = Σ f × r        v = Σ |f| × |r|        per address and date t, over its markets
```

n counts the nonzero flows behind u and v. Address contributions are summed within each entity. The score for D
pools every market:

```
score(D) = Σ u ÷ Σ v      over the entity's contribution dates t from D - 30 to D - 1
```

It is defined when Σ v > 0 and the entity has at least 5 nonzero flows in the window. A score of +1 means every
position change went in the direction of the previous day's return; -1 means every change went against it. Flows
dated D or later never classify D. An entity without a score is in neither style cohort.

**Robustness cohorts.** `non_mm_s070`, `non_mm_s090`, `non_mm_t020` and `non_mm_t100` are `all` minus the market
makers under the four robustness rules of section 5. They are never published as daily cells and enter only the
robustness grid of section 17.

## 7. Truth metrics

Registered in [§6 Truth metrics](preregistration.md#6-truth-metrics); dtl in
[§9 Second target](preregistration.md#9-second-target-later-liquidations). Code: `truth/metrics.py`,
`truth/build.py::truth_cells`, `pipeline.py::with_dtl`. Deviation 2 applies to dtl.

A cell is a date, a market and a cohort. Each entity holding in the cell carries its notional (the absolute value of
its netted position), its sign, its address-level gross (the sum of the absolute notional of its addresses'
positions in the market), its fragile address-level notional, a co-held flag and a leverage weight w. An entity whose
positions net to zero is not in the cell. For a cell with n entities, L the notional of its long entities and S the
notional of its short entities:

```
ni             = (L - S) ÷ (L + S)
ai             = (n_long - n_short) ÷ n
lwi            = Σ sign × notional × w ÷ Σ notional × w
abs_ni         = |ni|
top10_share    = notional of the k largest entities ÷ (L + S),   k = ceil(0.10 × n)
fragile_share  = Σ fragile address-level notional ÷ Σ address-level notional
coheld_share   = notional of co-held entities ÷ (L + S)
dtl            = |L - S| ÷ mean daily taker notional over the archived days of [c - 7d, c)
```

| Metric | Type | Reading |
|---|---|---|
| `ni` | signed | net positioning; ni(non_mm) is the primary truth |
| `ai` | signed | counts entities, so a large holder counts the same as a small one |
| `lwi` | signed | weights each holding by its entity's effective leverage |
| `abs_ni` | unsigned | how one-sided the cohort is |
| `top10_share` | unsigned | concentration |
| `fragile_share` | unsigned | notional close to liquidation |
| `coheld_share` | unsigned | notional held by entities with the same direction across many markets |
| `dtl` | published, never scored | days the market's recent taker volume would take to trade the cohort's net position |

Definitions behind the formulas:

- **Leverage weight.** w = min(G ÷ AV, 50). G is the entity's address-level gross notional summed over every market,
  on any dex, in which it holds a net position that day; AV is its account value on D (section 6). w = 50 when AV is
  zero, negative or missing. lwi is missing when its denominator is not positive.
- **Fragile.** An address-level position is fragile when |mark - liquidation price| ÷ mark ≤ 0.10, with the market's
  snapshot mark. A position without a liquidation price is not fragile. Fragility is measured on address-level
  positions, each with its own liquidation price, before netting. The denominator is the cell's address-level gross,
  which includes the offsetting legs of netted entities.
- **Largest entities.** Entities are ranked by notional, ties broken by entity identifier. k is computed without
  floating-point noise (0.10 × 30 gives 3, not 4).
- **Co-held.** A holding is co-held when its entity holds net positions in the same direction (long or short) in at
  least 5 other eligible markets, on any dex, that day (eligibility in section 9; the market itself is not counted).
  The flag belongs to the entity and market, so it is the same in every cohort.
- **dtl.** The denominator is the market's mean daily taker notional over the days of [c - 7d, c) that have
  archived fills (`volume_7d_archived_mean`; a day missing from the archive is left out of the mean rather than
  counted as zero; deviation 2). Each cohort uses its own net notional. dtl is missing when that volume is zero or
  missing, and it exists for main-dex and `xyz` markets, where proxies are computed.
- **Internal.** `top2_share`, the notional of the two largest entities ÷ (L + S), drives the disclosure rule and is
  never published.

`gross_notional` = L + S and `net_notional` = L - S are published with each cell, with `n_entities`, the market's
open interest on D (`oi_notional`), `eligible` and `cutoff`. Because netting preserves net notional, the identities of
sections 1 and 4 hold at the entity level.

## 8. Proxies

Registered in [§7 Proxies, timing and the primary panel](preregistration.md#7-proxies-timing-and-the-primary-panel).
Code: `proxies/native.py`, `proxies/binance_px.py`, `pipeline.py::stage_proxies`.

Every window ends at the cutoff c. Two building blocks:

- P(t), the last trade price before t: the close of the latest hourly bar that ended at or before t. It is missing
  if no bar ended in the 24 hours before t.
- OI(D - k), open interest in contracts (Σ size of long positions) at the snapshot dated D - k. Contracts keep price
  moves out of open-interest changes. "OI notional" below is long notional at that snapshot (section 2.2).

**Hyperliquid-native proxies** (main-dex and `xyz` markets):

| Proxy | Family | Definition | Missing when |
|---|---|---|---|
| `premium_24h` | confirmatory | mean hourly premium over [c - 24h, c) | fewer than 12 hourly rows |
| `momentum_7d` | confirmatory | ln(P(c) ÷ P(c - 7d)) | either price is missing |
| `oi_flow_7d` | confirmatory | sign(momentum_7d) × ln(OI(D - 1) ÷ OI(D - 8)) | either input is missing |
| `funding_24h` | secondary directional | mean settled hourly funding over [c - 24h, c) | fewer than 12 rows |
| `funding_7d` | secondary directional | mean settled hourly funding over [c - 7d, c) | fewer than 84 rows |
| `abs_premium_24h` | unsigned | \|premium_24h\|, the absolute value of the 24-hour mean | as `premium_24h` |
| `oi_change_7d` | unsigned | ln(OI(D - 1) ÷ OI(D - 8)) | either snapshot is missing or its OI is not positive |
| `liq_intensity_7d` | unsigned | liquidated notional in hourly bars inside [c - 7d, c) ÷ OI notional at D - 1 | OI notional at D - 1 is missing or not positive |
| `oi_to_volume_7d` | unsigned | OI notional at D - 1 ÷ mean daily taker notional over [c - 7d, c) (the 7-day sum ÷ 7) | the 7-day taker notional is zero, or OI notional at D - 1 is missing |
| `comovement_30d` | unsigned | Pearson correlation between the market's 30 daily log returns ending at c (from prices P(c - k days), k = 0 to 30) and the equal-weighted mean return of the other markets in its group on the same days | fewer than 20 days with both returns, or either series is constant |
| `rv_7d` | control | sqrt(24 × sample variance of the hourly log returns of the bars inside [c - 7d, c)) | fewer than 84 returns |

An hourly return is ln(close ÷ the previous bar's close). An hour without a taker trade has no bar, so a return can
span more than one hour. Also published with the native proxies: `pinned_share_24h` and `pinned_share_7d` (the
share of funding rows equal to the baseline 0.0000125 per hour, which is 0.01% per 8 hours, with the same windows and
minimum counts as funding), `liq_long_7d`, `liq_short_7d`, `volume_7d_mean`, `oi_prev_notional` and three
descriptions of each seven-day fill window: `flow_coverage_7d` (the share of the window on days with archived fills),
`flows_complete_7d` (every day archived) and `volume_7d_archived_mean` (mean daily taker notional over the archived
part). Minimum counts are half a window's hours (12 of 24, 84 of 168).

**Days missing from the archive.** The archive lacks 38 single days of fills between August 2025 and August 2026,
so only 109 of the 305 evaluation snapshots have a complete seven-day window behind them. As registered, the flow
proxies (`liq_intensity_7d`, `oi_to_volume_7d`) count a missing day as a day without volume or liquidations; the
results also rescore them on complete windows only (deviation 4). Prices come from the latest hourly bar ending in
the previous 24 hours, so a price just after a missing day can be up to a day old. Open interest is taken only from
snapshots captured before the scored snapshot's cutoff (`pipeline.oi_timing`); in this archive that check removes
nothing.

**Binance proxies** (primary panel only):

| Proxy | Family | Definition | Missing when |
|---|---|---|---|
| `bn_global_ls` | confirmatory | ln of `count_long_short_ratio` (global account long/short ratio) in the last Vision metrics row stamped before c | no row stamped in [c - 65 min, c), or the ratio is not positive |
| `bn_top_account_ls` | confirmatory | ln of `count_toptrader_long_short_ratio` (top-trader account ratio), same row | as above |
| `bn_top_position_ls` | confirmatory | ln of `sum_toptrader_long_short_ratio` (top-trader position ratio), same row | as above |
| `bn_taker_ls` | confirmatory | ln(Σ taker buy volume ÷ Σ taker sell volume) over 5-minute klines opening in [c - 24h, c); sell volume = volume - taker buy volume | no kline in the window, or either sum is not positive |
| `bn_funding_24h` | secondary directional | mean of the funding rates settled in [c - 24h, c), as published (not rescaled by settlement interval) | no settlement in the window |
| `bn_oi_change_7d` | unsigned | ln(`sum_open_interest` in the last row stamped before c ÷ the same in the last row stamped before c - 7d) | either row fails the staleness rule, or either value is not positive |

A Vision metrics row stamped T holds the values of Binance's REST row stamped T + 5 minutes (the mapping was checked
against the REST endpoints; `sources/binance.py`), so its information is as of T + 5 minutes. The timing rule
applies to the stamp. Rows are stamped every 5 minutes, so an admissible row is stamped c - 5 min or earlier and
reflects the market at c or earlier. A ratio is missing when its information is more than 60 minutes old at c, that
is, when no row is stamped in [c - 65 min, c). The same rule applies at c - 7d to the back leg of
`bn_oi_change_7d`. Kline volumes are in base-asset units. Hyperliquid's `kPEPE` and `kBONK` map to Binance's
`1000PEPEUSDT` and `1000BONKUSDT`.

**Families and expected signs.** Every directional proxy is expected to move with the signed truths (expected sign
positive). The seven confirmatory proxies form the Holm family of section 14. Funding is secondary: on this venue
funding is the premium clamped to an interest rate, so it can sit exactly at the baseline rate, and a premium that
scores at least as well as funding is mechanical, not a finding. As registered, each funding row shows its share of
pinned hours (`pinned_share`, the mean over the market-days scored): `pinned_share_24h` for `funding_24h`,
`pinned_share_7d` for `funding_7d`, and for `bn_funding_24h` the share of Binance settlements at the interest-rate
floor of 0.01% per 8 hours, scaled to each settlement's interval (`bn_pinned_share_24h`).

**Exclusions and special pairings.**

- `momentum_7d` and `oi_flow_7d` are never scored against `trend` or `contrarian`, which are defined by the same
  flow-return relation.
- The one exception is a positive control: `momentum_7d` against ni(trend) must be above zero. A value at or below
  zero flags a pipeline error; the report prints the flag, and the pipeline does not stop.
- `comovement_30d` is scored only against `coheld_share`, and, as registered, against "the other markets in the
  panel": the study's tables recompute it with the primary panel's 30 markets as the group for primary-panel tables
  and with the all-market panel for all-market tables and target (b) (`pipeline.with_panel_comovement`). The
  `comovement_30d` column of the published `proxies.parquet` is a general-purpose version whose group is every
  main-dex market on the date (for a stock, the other US stock perpetuals of section 19).
- Cross-venue agreement pairs each Binance ratio with the observed quantity it claims to measure: `bn_global_ls`
  with ai(all), `bn_top_account_ls` with ai(top_av), `bn_top_position_ls` with ni(top_av).

## 9. Eligibility and the scored panels

Registered in [§7](preregistration.md#7-proxies-timing-and-the-primary-panel),
[§8 Statistics and verdicts](preregistration.md#8-statistics-and-verdicts) and
[§12 Publication and privacy](preregistration.md#12-publication-and-privacy). Code: `truth/build.py::truth_cells`,
`pipeline.py::scored_cells`, `pipeline.py::all_market_panel`, `grading/scorecard.py::primary_family`.

- **Eligible market-days.** A market-day is eligible when open interest at the snapshot dated D - 1 is at least
  $1,000,000. If D - 1 has no snapshot, no market is eligible on D. Every truth value that enters a statistic comes
  from an eligible market-day. Proxies are not filtered separately: a statistic that pairs a proxy and a truth on the
  same day needs both values, so it uses eligible days only. Target (b) (section 18) restricts its proxy predictors
  to the same eligible market-days explicitly. The one exception is the placebo (section 17), which pairs a proxy
  with the truth 60 days away. The published panel also carries ineligible cells that pass disclosure control,
  flagged `eligible = false`.
- **Primary panel.** The 30 largest main-dex markets by open interest on the 2025-08-27 snapshot, the day before
  evaluation starts, among those with a USDT-margined Binance perpetual (Binance Vision publishes its daily metrics
  file for that date). The selection is frozen in `data/primary_panel.json`: ETH, BTC, HYPE, SOL, ENA, XRP, PUMP,
  FARTCOIN, LINK, DOGE, SUI, kPEPE, AAVE, PENGU, WLFI, LTC, BNB, AVAX, kBONK, CRV, ADA, LDO, XPL, IP, TRUMP, ARB,
  WLD, TON, ZORA and TRX. Two larger markets, LAUNCHCOIN and MNT, were skipped because Vision has no metrics file
  for them on that date.
- **Common support (every primary-panel table).** A market-day is kept only if ni(non_mm) and all seven
  confirmatory proxies are present. A market needs at least 60 such days, and dates on which no kept market has a
  kept day are dropped. As registered ("all proxies are scored on the same market-days"), every table on the
  primary panel, the secondary and unsigned proxies, cross-venue agreement, the positive control and the
  robustness cohorts included, is scored on these market-days (`scorecard.support_table`); each row still needs its
  own truth on them.
- **All-market panel (secondary).** Main-dex markets with at least 60 eligible non_mm market-days. Secondary
  statistics use pairwise support: the market-days on which that proxy and that truth are both present.
- **Minimum sizes.** A market enters a time-series statistic with at least 60 paired days. A day enters a
  cross-sectional statistic with at least 10 markets.
- **Thin data.** A proxy below its minimum-data rule (section 8) is missing for that market-day rather than computed
  from thin data.

## 10. The primary statistic

Registered in [§8 Statistics and verdicts](preregistration.md#8-statistics-and-verdicts). Code:
`grading/inference.py::joint_ts`, `grading/scorecard.py::primary_family`.

For each confirmatory proxy p and each market m of the primary panel, ρ_m is the Spearman correlation between p and
ni(non_mm) over the market's common-support dates (average ranks for ties). A market enters when it has at least 60
paired days and neither series is constant. The statistic is the equal-weighted mean across markets on Fisher's z
scale:

```
z̄ = (1 ÷ M) × Σ_m atanh(ρ_m)        ρ_m clipped to ±0.999999, M markets
ρ̄ = tanh(z̄)
```

The primary table reports z̄ (`z`), ρ̄ (`rho`), the markets used (`n_markets`) and the paired market-days (`n_obs`).
Correlating within each market over time asks whether the proxy moves with that market's own positioning;
differences in level between markets do not enter. Equal weights give every market the same say, whatever its size
or number of days.

## 11. The bootstrap

Registered in [§8 Statistics and verdicts](preregistration.md#8-statistics-and-verdicts). Code:
`grading/stats.py::block_indices`, `grading/inference.py::politis_white_block`, `block_length`, `joint_ts`.

**Resampling.** Circular block bootstrap over dates. The T common-support dates are taken in order as rows; calendar
gaps are not filled. Blocks of b consecutive rows start at uniformly drawn rows, wrap from the last row to the first,
and are concatenated and cut to T rows. The unit is the date, a full row of the date × market matrix, so dependence
across markets on a given day is kept. Each replicate recomputes the within-market ranks on the resampled rows, so a
date drawn twice ties with itself, and gives a replicate z̄* over the markets used in the estimate (a market whose
resampled correlation is undefined is left out of that replicate). The 60-day rule selects the markets of the
estimate; inside a replicate every such market stays in the mean however few of its days were drawn, which widens
the interval for a market with short support (a conservative property of the registered code). There are 2,000
replicates with seed 20261117. The
same draws serve all seven proxies, so intervals, ranks and pairwise comparisons come from the same resamples.

**Block length.** For every series in the panel (the truth and each proxy, in each kept market, missing values
dropped, at least 11 values and not constant), compute the Politis-White (2004) optimal block length for the
circular bootstrap, with the Patton, Politis and White (2009) correction. The tests check this against the `arch`
package. b is the median across series, rounded to an integer and clipped to [20, 40]; it is 20 if no series
qualifies. One b serves the whole primary family. It is reported in `results/score_summary.json` and
`primary_family.json`, with the number of common dates.

**Intervals and p-value.** The 95% interval is the 2.5th and 97.5th percentiles of tanh(z̄*). The 90% interval, used
for equivalence, is the 5th and 95th. The two-sided p-value locates zero in the bootstrap distribution:

```
p = min(1, 2 × min(A, B))
A = (number of z̄* ≤ 0, plus 1) ÷ (R + 1)
B = (number of z̄* ≥ 0, plus 1) ÷ (R + 1)       R = the number of defined replicates (2,000)
```

The interval and the p-value measure sampling uncertainty around the estimate; they do not simulate a null. The
rotation null of section 12 does.

## 12. The rotation null

Registered in [§8 Statistics and verdicts](preregistration.md#8-statistics-and-verdicts). Code:
`grading/inference.py::rotation_null`.

Two persistent series can show a large sample correlation by chance, and a bootstrap that resamples the observed
pairs together cannot reveal it. The rotation null breaks the alignment between proxy and truth while keeping each
series' persistence and the cross-market structure:

1. Draw an integer k uniformly from 60 to T - 61, where T is the number of panel dates. This is the registered range
   "between 60 and T - 60" with the upper end excluded, as numpy draws it. With 121 dates or fewer, k is always 60.
2. Shift the whole truth matrix circularly by k rows, the same k for every market.
3. Recompute z̄ with the usual rule (markets with at least 60 paired days after the shift).

There are 1,000 draws with seed 20261118 (the registered seed plus 1). Every proxy uses that seed, so all seven face
the same shifts. `rot95` is the 95th percentile of |z̄| across the defined draws. It is on the z scale and is compared
with |z̄|.

## 13. The fixed-effects slope

Registered in [§8 Statistics and verdicts](preregistration.md#8-statistics-and-verdicts). Code:
`grading/scorecard.py::fe_slope`, `grading/stats.py::twoway_fe_cluster`.

All common-support market-days are pooled, in levels. Truth and proxy are both purged of market means and date
means by alternating projections, which are exact for an unbalanced panel at convergence. The truth is then
regressed on the proxy:

```
β = Σ x̃ × ỹ ÷ Σ x̃²        x̃, ỹ = proxy and truth with market and date means removed
```

The standard error is clustered two ways, by market and by date (Cameron, Gelbach and Miller: the market-clustered
variance plus the date-clustered variance minus the variance clustered by market-date cell, each with a G ÷ (G - 1)
factor), computed on the demeaned regression and checked against statsmodels in the tests. It is undefined when the
combined variance is not positive. The slope needs at least 10 observations, 2 markets, 2 dates and a non-constant
proxy. It is in raw units: truth per unit of proxy.

Only the sign of β enters the verdict; β and its standard error are reported. The slope weights market-days by the
proxy's within variation instead of weighting markets equally, and it nets out common daily moves. A matching sign
is evidence that the verdict does not depend on the averaging scheme.

## 14. The four verdicts and Holm

Registered in [§8 Statistics and verdicts](preregistration.md#8-statistics-and-verdicts). Code:
`grading/inference.py::verdict`, `grading/stats.py::holm`.

Each confirmatory proxy gets one verdict against ni(non_mm). The expected sign is positive for all seven.

| Verdict | Rule |
|---|---|
| confirmed | z̄ > 0, Holm-adjusted p < 0.05, \|z̄\| > rot95, and sign(β) = sign(z̄) |
| reversed | z̄ < 0, with the same three tests |
| uninformative | not confirmed or reversed, and the 90% interval of ρ̄ lies strictly inside (-0.10, +0.10) |
| inconclusive | none of the above |

Confirmed and reversed require z̄, the Holm p-value, rot95 and β all to be defined. "Uninformative" is an
equivalence test on the ρ scale: a 90% interval inside ±0.10 is the two one-sided tests procedure at 5% each, and it
reads as rejecting |ρ̄| ≥ 0.10 at the 5% level. It uses the unadjusted interval; it is not corrected for the seven
tests.

**Holm.** The seven bootstrap p-values are adjusted together, a missing value counting as 1. Sort them,
p(1) ≤ … ≤ p(7). The adjusted value of the j-th is the maximum over i ≤ j of min(1, (8 - i) × p(i)).

The table is written to `results/binance/primary_family.csv` and `primary_family.json`, under the Binance licence,
because the family includes Binance proxies and its common support depends on them.

## 15. Ranks

Registered in [§8 Statistics and verdicts](preregistration.md#8-statistics-and-verdicts). Code:
`grading/inference.py::rank_table`, `outranks`.

Proxies are ranked by |z̄| (rank 1 is the largest), with the sign shown. In each bootstrap replicate the seven
proxies are ranked by |z̄*|. A proxy's 95% rank interval runs from the floor of the 2.5th percentile to the ceiling of
the 97.5th percentile of its replicate ranks.

For every pair of proxies, `primary_family.json` publishes the `outranks` matrix: `outranks[A][B]` is the share of
replicates in which |z̄*_A| > |z̄*_B|. Under the registered reading, A beats B only if that share is at least 0.95
(`rank_dominance`). Pairwise claims are made only when kill criterion 4 allows a ranking claim at all; they are
listed in `primary_family.json` under `dominance_claims` (empty otherwise), and the report prints them.

## 16. Stability and kill criterion 4

Registered in [§8 Statistics and verdicts](preregistration.md#8-statistics-and-verdicts) and
[§11 Gates and kill criteria](preregistration.md#11-gates-and-kill-criteria). Code:
`grading/scorecard.py::primary_family`, `grading/inference.py::stability`.

The common-support dates are split at their median. With the T dates in order, the split date h is the one at
position ⌊T ÷ 2⌋ counting from zero; the first half is the dates before h, the second half is h and after. In each
half, z̄ is recomputed on the same market-days with markets needing at least 60 days (the registered minimum for
any time-series statistic), 2,000 bootstrap
replicates, the same block length, and seeds 20261119 and 20261120. A half-sample standard error is the standard
deviation of that half's replicate z̄*.

- **Unstable.** A confirmed or reversed proxy is labelled unstable if its two half-sample z̄ differ in sign, or if
  |z̄₁ - z̄₂| ≥ 1.96 × sqrt(se₁² + se₂²). The label does not change the verdict.
- **Kill criterion 4.** Over the proxies that are confirmed or reversed, compute Kendall's τ (tau-b) between the
  first-half and second-half signed z̄. τ is computed only if there are at least 3 such proxies; otherwise the
  result is "no ranking claimed". A ranking is claimed only if τ ≥ 0.40. The criterion governs ranking claims only,
  not verdicts. Its result is recorded in `results/score_summary.json` and `primary_family.json`.

## 17. Secondary statistics and Benjamini-Hochberg

Registered in [§7](preregistration.md#7-proxies-timing-and-the-primary-panel) and
[§8 Statistics and verdicts](preregistration.md#8-statistics-and-verdicts). Code:
`grading/scorecard.py::score_pair`, `grading/stats.py`, `pipeline.py::grid`, `pipeline.py::stage_score`.

Every (proxy, truth, cohort) row reports:

| Statistic | Definition |
|---|---|
| Levels ρ, 95% interval, p | sections 10 and 11 on the row's support (the primary family's common support on the primary panel; pairwise support on the all-market panel), with a block length chosen for that pair by the same rule and the seed 20261117 plus the row's position in its grid |
| Changes ρ | the same statistic on day-over-day changes of proxy and truth, differenced only between consecutive dates; no bootstrap |
| Cross-sectional ρ | on each day with at least 10 markets, Spearman across markets; the Fama-MacBeth mean over days, with a Newey-West standard error (Bartlett kernel, 10 lags, no small-sample factor) and a normal p-value; needs 3 days |
| OI-weighted ρ | the per-market ρ_m averaged on the z scale with weights equal to each market's mean open interest over the cohort's eligible cells, then tanh |
| FE slope | section 13, on the row's support |
| Pinned share (funding rows) | the mean share of pinned hours (or Binance settlements at the floor) over the market-days scored |
| Sign agreement (signed truths) | share of market-days with both values nonzero on which sign(proxy) = expected sign × sign(truth), with a Wilson 95% interval |
| Placebo ρ | the levels statistic, without bootstrap, with the truth shifted 60 calendar days forward (the proxy on D meets the truth of D - 60) and 60 back (the truth of D + 60) within each market; the Fisher mean of the two |
| Tail lift (signed truths) | in each market with at least 20 pairs, the events are days with the proxy (times the expected sign) at or above its 90th percentile or at or below its 10th; a hit is the truth at or above its 80th percentile, or at or below its 20th, in the same direction; lift = pooled hits ÷ events ÷ 0.20, so 1 is no lift and 5 is perfect |

The grids:

| Grid (file name) | Proxies | Truths and cohorts | Markets |
|---|---|---|---|
| `directional_primary` | the 7 confirmatory and 3 secondary directional | ni, ai, lwi in non_mm, top_av, trend, contrarian; and ai(all) | primary panel |
| `directional_all_markets` | the 5 native directional | the same | all-market panel |
| `unsigned_primary` | the 6 unsigned | abs_ni, top10_share, fragile_share, coheld_share in the same four cohorts | primary panel |
| `unsigned_all_markets` | the 5 native unsigned | the same | all-market panel |
| `cross_venue` | the 3 Binance ratios | as in section 8 | primary panel |
| `positive_control` | `momentum_7d` | ni(trend) | both panels |
| `robustness_cohorts` | the 7 confirmatory | ni in the 4 robustness cohorts | primary panel |
| `sensitivity_complete_flows` | `liq_intensity_7d`, `oi_to_volume_7d`, kept on complete seven-day windows only | the unsigned truths in the four cohorts | both panels |

The exclusions of section 8 apply throughout. Primary-panel rows are scored on the primary family's market-days;
the rows for the confirmatory proxies against ni(non_mm) still use their own block lengths and bootstrap draws, so
their intervals need not equal the primary family's.

**Benjamini-Hochberg.** Rows are flagged within each grid of the table above (the positive-control file holds two
one-row grids, one per panel), on the levels p-value at q = 0.05, a missing value counting as 1. With the m p-values
sorted, k* is the largest k with p(k) ≤ k × 0.05 ÷ m, and every row with p ≤ p(k*) is flagged. A grid is flagged as a
whole and then split by licence: its rows with a Binance proxy go to `results/binance/` under the same file name, the
rest to `results/`. Secondary results are labelled exploratory.

**Published-panel rerun.** The primary family is recomputed on the published panel (suppressed and rounded,
eligible rows only) and reported in `primary_family.json` beside the registered result, labelled "in-sample,
published panel". It is flagged "not representative" when more than 5% of primary market-days (eligible non_mm cells
of the primary panel) are suppressed.

## 18. Target (b): later liquidations

Registered in [§9 Second target: later liquidations](preregistration.md#9-second-target-later-liquidations). Code:
`grading/liquidation.py`, `pipeline.py::score_liquidations`.

**Outcome.** For snapshot D and market m, the window starts at the capture time rounded up to the next full hour
(unchanged if the capture is exactly on the hour) and runs 7 days. It sums the hourly liquidation bars that start
inside the window:

```
fwd_liq_intensity = liquidated notional ÷ OI notional at D
fwd_liq_signed    = (long liquidated - short liquidated) ÷ (long liquidated + short liquidated)
```

A window that touches any UTC date without an archived fill file is dropped. `fwd_liq_intensity` needs positive open
interest at D; `fwd_liq_signed` needs some liquidated notional. Outcomes are taken only for snapshots of the
evaluation window.

**Scoring.** Markets are the all-market panel. Every predictor, proxy or truth, and the `rv_7d` control enter only
on eligible market-days that have a non_mm cell; comovement is measured against the all-market panel.

- Unsigned predictors (the 5 native unsigned proxies, and abs_ni, top10_share, fragile_share and coheld_share) are
  scored against `fwd_liq_intensity`.
- Signed predictors (the 5 native directional proxies, and ni, ai and lwi) are scored against `fwd_liq_signed`.

Each pair is scored by the daily cross-sectional Spearman correlation across markets (days with at least 10
markets), averaged over days, with a Newey-West standard error (10 lags) and a normal p-value; it needs 3 days.
Scoring each day across markets removes market-wide days, as day fixed effects would. Every unsigned row is also
reported as a partial correlation controlling for `rv_7d`: within each day, the ranks of predictor and target are
each regressed on the rank of `rv_7d` (with an intercept), and the residuals are correlated. The registered rows
are flagged with Benjamini-Hochberg at q = 0.05, on the p-value of the plain (not partial) correlation. Rows with
`variant` "complete windows" rescore the two flow proxies on complete seven-day windows only (deviation 4); they
are a sensitivity check and are not flagged.

**Δρ.** The outcome table's dates are split at their median. In each direction, the native unsigned proxy with the
highest mean daily cross-sectional Spearman against `fwd_liq_intensity` on one half is chosen. On the other half, Δρ
is the mean over days of the daily ρ of fragile_share(non_mm) minus the daily ρ of the chosen proxy, over days on
which both exist. On a given day both correlations use the same markets: those where fragile_share, the proxy and
the outcome are all present. The 95% interval comes from a circular block bootstrap of the daily differences with a fixed block of 20
days (`bootstrap_block_min`, not a Politis-White choice), 2,000 replicates and seed 20261117. Both directions are
reported.

**dtl.** dtl (section 7) is published but the pipeline does not score it. It is never scored against
`oi_to_volume_7d`, which shares its denominator: `herdbench.grade()` accepts dtl as a truth and then drops
`oi_to_volume_7d` from its default baselines.

## 19. Transfer to US single-stock perpetuals

Registered in [§10 Transfer and futures bridges](preregistration.md#10-transfer-to-us-single-stock-perpetuals-and-futures-bridges)
and, for kill criterion 3, [§11 Gates and kill criteria](preregistration.md#11-gates-and-kill-criteria). Code:
`universe.py`, `pipeline.py::transfer_panel`, `score_transfer`, `grading/transfer.py`, `grading/power.py`,
`proxies/equity.py`, `sources/finra.py`.

**Stocks.** The 49 `xyz` markets classified as US-listed common stocks (`universe.US_STOCKS`). The classes come from
FINRA's issue names at the 2026-08-14 settlement, checked against each perpetual's price: a perpetual tracks the
instrument it names only if its mark matches that instrument's price. ADRs, ETFs, preferred shares, non-US and
unlisted names are excluded. A stock is matched to FINRA's records by its ticker, the part after `xyz:`.

**Unit.** A stock and a FINRA settlement date. The 17 settlement dates from 2025-12-15 to 2026-08-14, with their
publication dates from FINRA's schedule, are fixed in `grading/transfer.py`.

**Truth and pairings.** Truth is ni(non_mm) at a paired snapshot, on eligible market-days only. There are two
pairings:

- **Settlement.** The first snapshot captured at or after 16:00 New York time on the settlement date (daylight
  saving handled). This measures contemporaneous agreement between equity short sellers and perpetual traders.
  Short interest is published days later, so the pairing cannot be used in real time; it is exempt from the cutoff
  rule by name.
- **Publication.** The first snapshot captured at or after the end of FINRA's publication day (midnight New York
  time). This is the real-time version.

**Proxies.** With c the cutoff of the paired snapshot and span the number of days since the previous FINRA
settlement date (`grading/transfer.previous_settlement`; the first settlement in the panel, 2025-12-15, follows
2025-11-28, so its span is 17 days):

| Proxy | Definition | Expected sign |
|---|---|---|
| `si_change_pct` | FINRA's percent change in short interest since the previous settlement | negative |
| `days_to_cover` | FINRA's days to cover | negative |
| `log_short_interest` | ln(short interest in shares) | negative |
| `funding_period` | mean hourly funding over [c - span, c) | positive |
| `premium_period` | mean hourly premium over [c - span, c) | positive |
| `momentum_period` | ln(P(c) ÷ P(c - span)) | positive |
| `comovement_30d` | as in section 8, within the stock group, ending at c | positive |

The FINRA values are the same in both pairings. The perpetual-side proxies use the paired snapshot's cutoff, so
they differ between pairings. `funding_period` and `premium_period` have no minimum count of hourly rows.

**Model.** For each proxy, keep the stock-dates with ni and the proxy present; the model needs at least 10 rows,
3 stocks and a non-constant proxy. Remove stock means and period means from both variables (alternating
projections), divide each by its standard deviation, and fit a slope through the origin. The result is a
standardised two-way fixed-effects slope; because both variables are standardised, it equals the correlation of the
two demeaned series. Its standard error is clustered by stock (CR1), and its 95% interval uses Student's t with
G - 1 degrees of freedom, G being the number of stocks. A standard error clustered by stock and period is reported as
a robustness check. `direction_as_expected` records whether the slope has the expected sign. The p-value (t with
G - 1 degrees of freedom) is reported only if kill criterion 3 passes.

**Kill criterion 3.** Power is simulated for each pairing on the panel's actual stock × period pattern of observed
cells, with seed 20261117. Each of 2,000 draws builds a proxy x and an error e as independent copies of one process: an
AR(1) within stock with coefficient 0.5 and unit variance, plus a standard normal period shock common to all stocks,
plus a standard normal stock effect. The truth is y = 0.2 × x + sqrt(1 - 0.2²) × e. The draw keeps only the observed
cells, removes stock and period means, fits the slope and rejects at 5% (two-sided) with a stock-clustered standard
error and t(G - 1). Power is the share of draws that reject. Below 0.80, that pairing reports estimates and intervals
only. Kill criterion 3 covers the transfer only.

Outputs: `results/finra/transfer_settlement.csv`, `transfer_publication.csv` and `transfer.json`.

## 20. CFTC futures bridges

Registered in [§10 Transfer and futures bridges](preregistration.md#10-transfer-to-us-single-stock-perpetuals-and-futures-bridges).
Code: `sources/cftc.py`, `fetch.py`, `grading/bridges.py`, `pipeline.py::score_bridges`.

Each perpetual is paired with the CFTC futures contract that carries the same exposure:

| Perpetuals | CFTC contracts | Report | Group |
|---|---|---|---|
| `xyz:GOLD`, `xyz:SILVER`, `xyz:COPPER` | gold, silver, copper (COMEX) | Disaggregated | managed money |
| `xyz:PLATINUM`, `xyz:PALLADIUM` | platinum, palladium (NYMEX) | Disaggregated | managed money |
| `xyz:CL`, `xyz:NATGAS` | WTI crude oil, natural gas (NYMEX) | Disaggregated | managed money |
| `xyz:SP500`, `xyz:XYZ100` | E-mini S&P 500, Nasdaq-100 mini (CME) | Traders in Financial Futures | leveraged funds |
| `xyz:EUR`, `xyz:JPY`, `xyz:GBP` | Euro FX, Japanese yen, British pound (CME) | Traders in Financial Futures | leveraged funds |
| `BTC`, `ETH` | Bitcoin, Ether cash-settled (CME) | Traders in Financial Futures | leveraged funds |

```
net_share = (group long - group short) ÷ total open interest        (futures-only reports)
```

For the Traders in Financial Futures contracts, the asset managers' net share is reported in a second table.

Positions are as of the report date, normally a Tuesday. Truth is ni(non_mm) at the first snapshot captured at or after
00:00 UTC on the following day, on eligible market-days only, so neither side sees the other's later data. For each
market with at least 8 weeks and variation in both series, the bridge reports the Spearman correlation across weeks
with scipy's p-value. That p-value treats weeks as independent, so it is labelled descriptive. There is no
multiple-testing correction and no verdict. Outputs: `results/cot_bridges.csv` and
`results/cot_bridges_asset_managers.csv`.

## 21. Gates and kill criterion 1

Registered in [§11 Gates and kill criteria](preregistration.md#11-gates-and-kill-criteria). Code:
`gates/reconcile.py`, `gates/criteria.py`, `pipeline.py::stage_gates`.

**Reconciliation (covers every result).** Every pair of snapshots on consecutive dates t - 1 and t in the archive,
inside the evaluation window or not, is checked when the fill files for both dates exist. All accounts are included,
protocol accounts too. For each address and market:

```
Δ = size_t - size_{t-1}                (an absent position counts as zero)
F = Σ signed fill sizes (buy +, sell -) of perpetual fills timestamped in (capture_{t-1}, capture_t]
reconciled if |Δ - F| ≤ 10⁻⁶ × max(|size_t|, |size_{t-1}|) + 10⁻⁹
weight = max(|Δ|, |F|) × mark
```

Fills are read from the dates of both captures and from t - 1 and t. The mark is the current snapshot's, or the
previous one's for a market that disappeared; a row in a market with no mark in either snapshot carries zero weight.
A change without fills and fills without a change are both mismatches. The share of reconciled weight is pooled over
all pairs, and the gate passes at 0.99 or more. The share is also reported for market-maker addresses (every address
of every entity in the main rule's set on t), for everyone else, and for the worst pair. Market-maker sets exist only
for evaluation dates, so pairs on dates without a set, including every pair outside the evaluation window, count
wholly under everyone else.

**Gate 0 (covers every result).** Each trade has one maker and one taker, so the two notionals must match. For each
market, on any dex, and each fill day with nonzero volume, gap = |taker notional - maker notional| ÷ max(taker
notional, maker notional). The gate passes if at least 99% of market-days have a gap of at most 1%.

The gates run right after the truth stage (`herdbench build all`). If either gate fails, `results/gates.json`
carries the label
"fills incomplete", every result file carries it in a `label` column or field, `score_summary.json` makes ai(all),
which counts entities rather than notional, the headline check, and the report says so above the primary table.
The pre-registration ties the label to the reconciliation gate; applying it when Gate 0 fails as well is stricter.

**Kill criterion 1 (reported, not gating).** The number of the 49 stock perpetuals with at least 183 days between
their first and last day with an archived fill, against a bar of 25. The pre-registration records that it is known
to fail (21 against 25).

**Not run.** The Barone-Lillo TWAP appendix, which gated nothing, was not run (deviation 1). Kill criteria 5 to 7 of
the original plan are not code and are not assessed. The pre-registration's 10-pair reconciliation sample was an
integrity check made before registration; for the full-archive gate results, see [docs/results.md](results.md).

## 22. Disclosure control

Registered in [§12 Publication and privacy](preregistration.md#12-publication-and-privacy). Code:
`truth/privacy.py`, `truth/build.py::truth_cells`, `truth/build.py::publishable`, `api.py`.

Disclosure rules decide what is published. They never decide what is analysed.

1. **Cell rule.** A cell is publishable only if it has at least 20 entities and its two largest entities hold at
   most 80% of its gross notional.
2. **Complement rule.** For each nested pair on a market-day (`all` ⊇ `non_mm`; `non_mm` ⊇ `top_av`, `trend`,
   `contrarian`), the entities in the larger cell but not in the smaller one must themselves pass the cell rule;
   otherwise the larger cell is suppressed. Without this rule, a suppressed set could be recovered by subtracting one
   published cell from another. The registered text speaks of pairs published on a market-day; the code checks every
   pair whose difference holds at least one entity, whether or not the smaller cell is published. When the
   difference is empty, the two cells are identical and nothing can be recovered.
3. **Robustness cohorts** are never published.
4. **Rounding.** Ratios (`ni`, `ai`, `lwi`, `abs_ni`, `top10_share`, `fragile_share`, `coheld_share`, `dtl`) to 3
   decimals; `gross_notional`, `net_notional` and `oi_notional` to 2 significant figures; `n_entities` rounded down
   to a multiple of 10.
5. **Never published.** Any address, the entity map, entity-level rows, `top2_share`, the suppression flags and any
   Binance-derived proxy value. Native proxies are published.

**Why the analysis uses every cell.** Whether a cell is suppressed depends on how many entities hold the market and
how concentrated they are. That is related to what the truth metrics measure: thin, concentrated market-days are
suppressed more often, and a market can move in and out of publication over time. Scoring only published cells would
select the sample on the truth, and rounding would add noise. The registered statistics are therefore computed on
every eligible cell, unrounded, and ship once as fixed tables. How much was suppressed (overall, per cohort, and for
primary market-days with their share of open interest) is recorded in the addendum and in
`results/truth_panel_summary.json`. The published-panel rerun and its 5% flag (section 17) show how far the public
panel reproduces the registered result.

**Bring your own proxy.** `herdbench.grade()` and `herdbench grade` score a user's signal against the eligible cells
of the published panel, with the secondary statistics of section 17. The signal and the default native baselines are
scored on exactly the same market-days: those where the truth, the signal and every baseline are all present. Every
result is labelled "in-sample, published panel". A value for snapshot date D must use only information from before
that snapshot's `cutoff`.

## 23. Constants set in code

These choices are fixed in the code but are not fields of `PREREG`, so the test that ties the pre-registration to
`PREREG` does not cover them. Several are stated in words in the pre-registration's text. Each is described in the
section shown.

| Constant | Value | Code | Section |
|---|---|---|---|
| Net position treated as zero | \|net\| ≤ 10⁻⁹ × the entity's address-level gross in the market | `truth/metrics.py::entity_positions` | 3 |
| Minimum rows, 24-hour funding and premium windows | 12 | `proxies/native.py::funding_features` | 8 |
| Minimum rows, 7-day funding window | 84 | `proxies/native.py::funding_features` | 8 |
| Minimum hourly returns for `rv_7d` | 84 | `proxies/native.py::price_features` | 8 |
| Lookback for P(t) | 24 hours | `proxies/native.py::price_at` | 8 |
| Baseline funding for the pinned shares | 0.0000125 per hour | `proxies/native.py::BASELINE_FUNDING` | 8 |
| Comovement groups | study tables: the scored panel's markets (`pipeline.py::with_panel_comovement`); published `proxies.parquet`: every main-dex market on the date, and the 49 US stocks (`pipeline.py::stage_proxies`) | `pipeline.py` | 8 |
| Vision lag added to the 60-minute staleness bound | 5 minutes | `proxies/binance_px.py::VISION_LAG` | 8 |
| Binance funding | averaged as published, not rescaled by settlement interval; pinned when equal to 0.01% per 8 hours scaled to the interval | `proxies/binance_px.py::funding_features` | 8 |
| Binance listing test for the primary panel | Vision publishes the symbol's daily metrics file for 2025-08-27 | `pipeline.py::select_primary_panel` | 9 |
| Fisher z clip | ±0.999999 | `grading/inference.py`, `grading/stats.py` | 10 |
| Series that enter the block-length median | at least 11 non-missing values, not constant | `grading/inference.py::block_length` | 11 |
| Fixed-effects slope | at least 10 observations, 2 markets, 2 dates, a non-constant proxy | `grading/scorecard.py::fe_slope` | 13 |
| Seed offsets | rotation null +1; half-samples +2 and +3; secondary rows + position in grid | `grading/scorecard.py`, `pipeline.py::grid` | 12, 16, 17 |
| Cross-sectional mean | at least 3 days with a defined daily correlation | `grading/stats.py::xs_statistic`, `grading/liquidation.py::xs_partial` | 17, 18 |
| Tail lift | at least 20 pairs per market | `grading/inference.py::tail_lift` | 17 |
| Target (b) window start | capture rounded up to the next full hour | `grading/liquidation.py::forward_liquidations` | 18 |
| Δρ bootstrap block | 20 days, fixed (`bootstrap_block_min`) | `pipeline.py::score_liquidations` | 18 |
| US stock list | 49 tickers | `universe.py::US_STOCKS` | 19 |
| FINRA settlement and publication dates | 17 pairs | `grading/transfer.py::FINRA_PUBLICATION` | 19 |
| Transfer periods | days since the previous FINRA settlement (2025-11-14 and 2025-11-28 precede the 17 pairs) | `grading/transfer.py::previous_settlement` | 19 |
| Transfer, publication instant | midnight New York at the end of FINRA's publication day | `pipeline.py::finra_publication_instant` | 19 |
| Transfer model | at least 10 rows, 3 stocks, a non-constant proxy | `grading/transfer.py::transfer_stats` | 19 |
| CFTC contract map | 14 contracts | `sources/cftc.py::BRIDGE`, `fetch.py::EXTRA_COT` | 20 |
| Bridge timing and minimum | first capture at or after 00:00 UTC the day after the report date; at least 8 weeks | `grading/bridges.py` | 20 |
| Reconciliation tolerance | 10⁻⁶ × max(\|size_t\|, \|size_{t-1}\|) + 10⁻⁹ | `gates/reconcile.py` | 21 |
| Open-interest timing guard | the D - 1 and D - 8 snapshots must be captured before the cutoff of D | `pipeline.py::oi_timing` | 8 |
| Pinned-share column per funding proxy | `funding_24h` → `pinned_share_24h`, `funding_7d` → `pinned_share_7d`, `bn_funding_24h` → `bn_pinned_share_24h` | `pipeline.py::PINNED` | 8 |
| Funding cross-check sample | BTC, ETH, SOL, HYPE, xyz:NVDA; one seeded 2-day window each; agreement to 10⁻¹² | `pipeline.py::check_funding` | 3 |
