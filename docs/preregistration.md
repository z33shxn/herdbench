# Pre-registration

**Registered:** 18 November 2026, before any proxy-versus-truth statistic was computed on these data.
**Study:** HerdBench, a measurement-validity study of public crowding and positioning proxies.
**Author:** Mohammed Zeeshan.

Every analysis choice is fixed here and lives in code as `herdbench.config.PREREG`; the test suite fails
if this document and the code disagree. A change after registration is reported under "Deviations" in
`docs/results.md`, with the reason, and the registered version is still run and shown. A timestamp proof
of the commit that registers this document is published with the repository (`docs/timestamps/`).

| Field | Value | Meaning |
|---|---|---|
| `registered_on` | 2026-11-18 | Date of registration |

## 1. Question and claims

Do cheap, public, aggregate signals (funding, premium, open interest, momentum, comovement,
liquidations, exchange long/short ratios, short interest) track how traders are actually positioned?

The test runs on Hyperliquid, a derivatives venue where every account's open position is public, so
positioning is observed for all accounts rather than inferred. Because every long has a short, the net
position of all accounts is zero. The primary truth is therefore the net positioning of the crowd that
remains after removing market makers and protocol accounts: `ni(non_mm) = −(market-maker net + protocol
net) ÷ non_mm gross`. It is called **net positioning**; "crowding" is kept for the unsigned truths.

The study measures agreement. It makes **no causal claim**, estimates no crowding "wedge" or capacity
curve, does not claim these accounts represent equity funds, and does not claim the books are complete
(the same traders may hold offsetting positions elsewhere).

## 2. Pre-analysis record: everything seen before registration

- **Integrity only.** File listings, schemas, row counts, the long-equals-short identity, field counts.
- **Reconciliation sample (no proxies involved).** On 10 consecutive snapshot pairs spread across the year,
  every account's change in position equalled the net of its fills timestamped between the two captures:
  100.000% of changed notional reconciled, about 1 million changes, zero mismatches, for market makers
  and for everyone else. Maker notional equalled taker notional in every market on every day checked.
- **Capture times.** 271 snapshots were captured between 00:00 and 01:00 UTC on their date, 62 later on
  that date (mostly a run from 2025-08-28 to 2025-12-16 at about 20:05 UTC), and one (2026-04-24) three
  seconds before midnight. Section 7's cutoff rule handles all three cases without look-ahead.
- **Kill criterion 1 is known to fail:** 21 US single-stock perpetuals have at least 183 days between
  their first and last archived fill, against a bar of 25. It is reported, not used as a gate.
- **The earlier pilot (3 November 2026)** took the 400 largest wallets by account value from the live API
  at one moment, without removing market makers or vaults; its proxy correlations had p-values between
  0.14 and 0.70. It is not evidence for or against any proxy here.
- **The entity map and the suppressed share** are recorded in a dated addendum (section 14), committed and
  timestamped before any proxy-versus-truth statistic is computed.

## 3. Data

| Field | Value | Meaning |
|---|---|---|
| `mirror_revision` | de05dc101c819aa68accfefd6647b8bf6de02189 | Hugging Face commit of `craftify2221/hyperliquid-fills-raw` used for every file |
| `evaluation_start` | 2025-08-28 | First evaluated snapshot date (earlier dates warm up trailing windows) |
| `evaluation_end` | 2026-08-18 | Last snapshot date in the archive |

One all-account snapshot per UTC date; when a date has two files (2026-03-24), the earlier capture is used.
Nothing is differenced across a missing date. Fills are reduced to 14 columns. Funding and premium come from
Ironflow's CC-BY copy of Hyperliquid's settled funding history (revision pinned in code), checked against the
venue's API on a sample. Hourly prices, volume and liquidations are built from the fills themselves.

## 4. Entities and protocol accounts

| Field | Value | Meaning |
|---|---|---|
| `protocol_addresses` | 0xdfc24b077bc1425ad1dea75bcb6f8158e10df303, 0x677d831aef5328190852e24f13c46cac05f984e7, 0x010461c14e146ac35fe42271bdc1134ee31c703a, 0x2e3d94f0562703b25c83308a05046ddaf9a8dd14, 0x2ed5c4484ea3ff8b57d5f2fb152a40d9f2b68308, 0x31ca8395cf837de08b24da3f660e77761dfb974b, 0x469f690213c467c39a23efacfd2816896009d7d8, 0x5e177e5e39c0f4e421f5865a6d8beed8d921cb70, 0xb0a55f13d22f66e6d495ac98113841b2326e9540 | The HLP vault, its leader and its child accounts, removed from every cohort |
| `backstop_liquidator_base` | 0x4000000000000000000000000000000000000000 | Builder-dex backstop liquidators are this address plus the dex index... |
| `backstop_liquidator_span` | 4096 | ...for offsets 0 to 4095; removed from every cohort |

Hyperliquid lets a master account run sub-accounts, so the unit of analysis is the **entity**: a master and
all of its sub-accounts. The venue's public `userRole` and `subAccounts` requests map every market-maker
candidate (the loosest rule below, in any window) and every address that is ever one of the three largest
holders of an eligible market. Every other address is its own entity, and so is every vault. The market-maker
rule, the cohorts, the disclosure rules and every truth metric are computed on entities. Within a market an
entity's sub-account positions are netted.

## 5. Who counts as the crowd

| Field | Value | Meaning |
|---|---|---|
| `mm_window_days` | 30 | Market-maker status uses fills from the 30 calendar days before the snapshot |
| `mm_maker_share` | 0.8 | Market maker: at least 80% of the entity's perp traded notional was passive (maker)... |
| `mm_min_notional` | 10000000 | ...and at least $10,000,000 traded... |
| `mm_min_active_days` | 10 | ...and active on at least 10 days... |
| `mm_turnover_multiple` | 50 | ...and traded at least 50 times its mean gross position notional over the window's snapshots (denominator floored at $1) |
| `mm_min_fill_days` | 15 | The window needs at least 15 days of archived fills... |
| `mm_carry_forward_days` | 15 | ...otherwise the last known set is used for at most 15 days, after which the date has no non_mm cohort |
| `mm_robustness_maker_shares` | 0.7, 0.9 | Robustness rules with other maker shares (statistics only, never published as daily cells) |
| `mm_robustness_turnover` | 20, 100 | Robustness rules with other turnover multiples (statistics only) |
| `top_av_fraction` | 0.2 | Top cohort: the top 20% of a market's rankable non_mm holders by account value... |
| `top_av_lag_days` | 8 | ...measured 8 days before the snapshot (so a rally cannot promote longs through unrealised profit)... |
| `top_av_lag_max_days` | 14 | ...or at the latest snapshot 8 to 14 days before; an entity without one is not ranked |
| `style_window_days` | 30 | Trend score uses flows dated in the 30 days before the snapshot |
| `trend_threshold` | 0.2 | Trend followers score at least +0.20; contrarians at most −0.20 |
| `style_min_flows` | 5 | A trend score needs at least 5 nonzero position changes |
| `cohorts` | all, non_mm, top_av, trend, contrarian | Published cohorts |
| `robustness_cohorts` | non_mm_s070, non_mm_s090, non_mm_t020, non_mm_t100 | Analysis-only cohorts |

`all` is every non-protocol entity; `non_mm` removes market makers; `top_av`, `trend` and `contrarian` are
drawn from inside `non_mm`, so no sub-cohort contains market-maker inventory. Real market makers turn their
inventory over many times a day; patient limit-order whales and carry traders, who are also mostly passive,
turn it over far less, which is what the turnover test separates. A trend score is Σ f·r ÷ Σ |f|·|r| over the
entity's flows f (change in size between consecutive snapshots × mark) and the market's previous
snapshot-to-snapshot log return r.

## 6. Truth metrics

| Field | Value | Meaning |
|---|---|---|
| `fragile_distance` | 0.1 | A position is fragile if its mark is within 10% of its liquidation price |
| `top_share_fraction` | 0.1 | Concentration: gross share of the largest 10% of a cell's entities |
| `lwi_leverage_cap` | 50 | Leverage weight = entity gross notional ÷ account value, capped at 50 (50 when the account value is not positive) |
| `coheld_min_other` | 5 | Co-held: the entity holds the same direction in at least 5 other eligible markets that day |
| `signed_truths` | ni, ai, lwi | Signed truth metrics |
| `unsigned_truths` | abs_ni, top10_share, fragile_share, coheld_share | Unsigned truth metrics |
| `primary_cohort` | non_mm | Primary cohort |
| `primary_truth` | ni | Primary truth metric (net positioning) |

`ni` = (L − S)/(L + S) on entity notional; `ai` = (entities long − entities short)/entities; `lwi` weights
each holding by the entity's effective leverage. `ni(all)` is not zero: it is the protocol accounts' book,
negated. `ai(all)` counts entities, so it barely depends on where the market-maker line falls; it is printed
next to every primary row.

## 7. Proxies, timing and the primary panel

| Field | Value | Meaning |
|---|---|---|
| `binance_markets` | 30 | Primary panel: the 30 largest main-dex markets by open interest... |
| `binance_selection_snapshot` | 2025-08-27 | ...on this snapshot, among those with a USDT-margined Binance perpetual |
| `binance_max_staleness_minutes` | 60 | A Binance ratio whose information is more than 60 minutes old at the cutoff counts as missing |
| `confirmatory_proxies` | premium_24h, momentum_7d, oi_flow_7d, bn_global_ls, bn_top_account_ls, bn_top_position_ls, bn_taker_ls | The confirmatory family (Holm-corrected), scored against signed truths; expected sign positive |
| `secondary_directional` | funding_24h, funding_7d, bn_funding_24h | Funding is the premium clamped to an interest rate, so it is secondary; each row shows its share of pinned hours |
| `unsigned_proxies` | abs_premium_24h, oi_change_7d, comovement_30d, liq_intensity_7d, oi_to_volume_7d, bn_oi_change_7d | Scored against unsigned truths; comovement only against coheld_share |
| `comovement_window` | 30 | Comovement: correlation of 30 daily returns with the average of the other markets in the panel... |
| `comovement_min_returns` | 20 | ...needing at least 20 valid returns |

**Cutoff.** Each snapshot's cutoff c is the last full UTC hour at or before its capture time. Every proxy for
that snapshot uses only data stamped before c: funding and premium in [c − 24h, c) or [c − 7d, c); prices,
volume and liquidations from fills before c; open interest from the snapshots one and eight days earlier
(never the snapshot being scored); Binance rows stamped before c. `momentum_7d` = ln P(c)/P(c − 7d);
`oi_flow_7d` = sign(momentum_7d) × ln(OI_{D−1}/OI_{D−8}); `bn_taker_ls` = ln(Σ taker buys ÷ Σ taker sells)
over Binance 5-minute klines in [c − 24h, c). "Basis" equals the premium on this venue, so it is scored once.
A premium that scores at least as well as funding is mechanical, since funding is a clamped premium.

**Primary panel.** All proxies are scored on the same market-days: a day is dropped for every proxy if any
confirmatory proxy or the truth is missing, and a market needs at least 60 such days. The all-market panel is
a secondary table. momentum_7d and oi_flow_7d are never scored against the trend or contrarian cohorts, which
are defined by that same flow–return relation; momentum_7d against ni(trend) is a positive control (above 0
expected; at or below 0 flags a pipeline error). Cross-venue agreement is reported separately:
bn_global_ls against ai(all), bn_top_account_ls against ai(top_av), bn_top_position_ls against ni(top_av).

## 8. Statistics and verdicts

| Field | Value | Meaning |
|---|---|---|
| `min_market_days` | 60 | A market enters a time-series statistic only with at least 60 days |
| `bootstrap_block_min` | 20 | Block length: the median Politis–White optimal block across the panel's series, at least 20... |
| `bootstrap_block_max` | 40 | ...and at most 40 days |
| `bootstrap_reps` | 2000 | Circular block bootstrap replications; date draws are shared by all proxies |
| `seed` | 20261117 | Random seed for every resampling step |
| `nw_lags` | 10 | Newey–West lags |
| `min_xs_markets` | 10 | A day enters a cross-sectional statistic only with at least 10 markets |
| `placebo_shift_days` | 60 | Placebo: truth shifted by ±60 days within market |
| `rotation_draws` | 1000 | Rotation null draws: every market's truth shifted by the same random number of dates... |
| `rotation_min_shift` | 60 | ...between 60 and T − 60 |
| `equivalence_bound` | 0.1 | "Uninformative" when the 90% interval lies inside ±0.10 (two one-sided tests) |
| `holm_alpha` | 0.05 | Holm-adjusted significance level for the confirmatory family |
| `rank_dominance` | 0.95 | "A beats B" only if A outranks B in at least 95% of bootstrap draws |
| `stability_tau_threshold` | 0.4 | Kill criterion 4: Kendall τ between the half-sample rankings... |
| `stability_min_verdicts` | 3 | ...computed only if at least 3 proxies are confirmed or reversed; otherwise "no ranking claimed" |
| `tail_proxy_fraction` | 0.1 | Tail lift: proxy in its within-market extreme 10%... |
| `tail_truth_fraction` | 0.2 | ...and truth in its same-direction extreme 20% (null probability 0.20) |

**Primary statistic.** For each proxy, the within-market Spearman correlation with the truth over time, averaged
across markets on Fisher's z scale (z̄, equal weights), with a 95% bootstrap interval and a two-sided p-value.

**Verdict.** *Confirmed*: expected sign, Holm p < 0.05, |z̄| above the 95th percentile of the rotation null, and a
two-way fixed-effects slope (clustered by market and date) of the same sign. *Reversed*: the same tests with the
opposite sign. *Uninformative*: the 90% interval lies inside (−0.10, +0.10). *Inconclusive*: none of these.
Proxies are ranked by |z̄| with the sign shown, each with a 95% rank interval from the same bootstrap draws.

**Stability.** The primary panel's dates are split at their median into two chronological halves. A confirmed or
reversed proxy that changes sign between halves, or whose half-sample z̄ differ by at least 1.96 standard errors
of the difference, is labelled unstable. Kendall τ between the signed-z̄ rankings governs ranking claims only.

**Secondary statistics.** The same statistic on daily changes; daily cross-sectional Spearman with a Fama–MacBeth
mean and Newey–West error; an open-interest-weighted mean of the market correlations; the two-way fixed-effects
slope; sign agreement with a Wilson interval; the placebo; tail lift. Secondary grids are flagged with
Benjamini–Hochberg at q = 0.05 and labelled exploratory.

## 9. Second target: later liquidations

| Field | Value | Meaning |
|---|---|---|
| `liquidation_horizon_days` | 7 | Liquidated notional in the 7 days after the capture, divided by open interest |

Liquidated notional counts fills where the archive marks the account as the liquidated party; a window that
touches a missing fill day is dropped. Unsigned proxies and truths are scored with daily cross-sectional Spearman
correlations, and every such result is also reported as a partial correlation controlling for rv_7d (realised
volatility of hourly returns before the cutoff). Signed ones are scored against (long − short)/(long + short)
liquidated notional. Δρ compares fragile_share with the best unsigned proxy chosen on one half and scored on the
other (both directions), with a block-bootstrap interval. Days-to-liquidate (dtl = |non_mm net notional| ÷ mean
daily volume) is published but never scored against oi_to_volume_7d, which shares its denominator.

## 10. Transfer to US single-stock perpetuals, and futures bridges

| Field | Value | Meaning |
|---|---|---|
| `transfer_start` | 2025-12-15 | First FINRA settlement date in the panel |
| `transfer_end` | 2026-08-14 | Last FINRA settlement date inside the archive |
| `equity_proxies` | si_change_pct, days_to_cover, log_short_interest, funding_period, premium_period, momentum_period, comovement_30d | Proxies on the stock panel |
| `equity_negative_sign` | si_change_pct, days_to_cover, log_short_interest | Short-interest proxies are expected to move against ni |
| `power_rho` | 0.2 | Kill criterion 3: effect size for the power simulation |
| `power_ar1` | 0.5 | Within-stock persistence in the simulation |
| `power_reps` | 2000 | Simulations |
| `power_threshold` | 0.8 | Required power for p-values; below it the transfer reports estimates and intervals only |

Stocks are the `xyz` markets classified as US-listed common stocks (`herdbench.universe`, from FINRA issue names,
checked against each perp's price). The unit is a stock and a settlement date; truth is the non_mm imbalance at the
first snapshot captured after the US close (16:00 New York time) on the settlement date. This pairing measures
**contemporaneous agreement between equity short sellers and perp traders**; it cannot be used in real time and
is exempt from section 7's cutoff by name. A real-time variant pairs each short-interest release with the first
snapshot after its publication. The model is a two-way fixed-effects slope with errors clustered by stock
(two-way clustering as a robustness check). CFTC Commitments of Traders positions (managed money or leveraged
funds) for metals, energy, equity indices, currencies and CME Bitcoin and Ether are compared week by week with the
matching perpetuals, as description only.

## 11. Gates and kill criteria

| Field | Value | Meaning |
|---|---|---|
| `reconciliation_threshold` | 0.99 | Gate (all results): at least 99% of changed notional reconciles between snapshots and fills |
| `gate0_max_gap` | 0.01 | Gate 0 (all results): maker and taker notional within 1%... |
| `gate0_min_share` | 0.99 | ...on at least 99% of market-days |
| `kc1_min_stocks` | 25 | Kill criterion 1 (reported): at least 25 US single-stock perps... |
| `kc1_min_days` | 183 | ...with at least 183 days between first and last archived fill |
| `gate1_theta` | -0.0177 | Appendix (not gating): Barone and Lillo's TWAP coefficient... |
| `gate1_se` | 0.001 | ...and its standard error |

If the reconciliation gate fails, every result is labelled "fills incomplete" and ai(all) becomes the headline
check. Scope: the reconciliation gate and Gate 0 cover every result; kill criterion 3 covers the transfer; kill
criterion 4 covers ranking claims. Criteria 5 to 7 of the original plan (outreach, a rival preprint, a dated
draft) are not code and are not assessed.

## 12. Publication and privacy

| Field | Value | Meaning |
|---|---|---|
| `min_oi_notional` | 1000000 | A market-day is eligible if open interest at the previous snapshot is at least $1,000,000 |
| `min_entities` | 20 | A published cell needs at least 20 entities... |
| `dominance_top2_max` | 0.8 | ...and its two largest entities may hold at most 80% of its gross notional |
| `publish_decimals` | 3 | Published ratios are rounded to 3 decimals... |
| `publish_count_floor` | 10 | ...entity counts are rounded down to a multiple of 10, and notional to 2 significant figures |
| `suppressed_flag_share` | 0.05 | The published-panel rerun is flagged "not representative" if more than 5% of primary market-days are suppressed |

The analysis runs on every cell; disclosure rules apply to publication only. A complement rule also applies: for
each nested pair published on a market-day (`all` ⊇ `non_mm`; `non_mm` ⊇ `top_av`, `trend`, `contrarian`), the
difference must itself pass both rules, otherwise the larger cell is suppressed. No non-protocol address and no
entity-level row is ever published. The "bring your own proxy" function scores against the published panel only
and labels every result "in-sample, published panel"; statistics computed before suppression ship once, as a
fixed table.

## 13. Deviations

Any departure from this document is listed in `docs/results.md` under "Deviations", with its reason and the result
of the registered version.

## 14. Pre-analysis addendum

Recorded after the entity map and the truth panel were built and before any proxy-versus-truth statistic:
see `docs/preregistration_addendum.md`.
