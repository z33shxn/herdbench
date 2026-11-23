# Pre-analysis addendum

**Recorded:** 2026-11-23 14:48 UTC, after the entity map and the truth panel were built and **before any proxy-versus-truth
statistic was computed**. Proxies had been computed from their own data; none had been compared with any truth.
This file is committed and timestamped (`docs/timestamps/`) before scoring starts, and the scoring code refuses
to run without it (`herdbench build score --strict` also requires the entity map below, by hash).

## Entity map (section 4)

| Item | Value |
|---|---|
| Addresses looked up (market-maker candidates and top-3 holders) | 4,655 |
| Roles answered | 4,655 (subAccount 923, user 3,676, vault 56) |
| Masters whose sub-account lists were read | 3,830 of 3,830 |
| Addresses mapped to a master | 5,503 |
| Entities with more than one address | 1,041 |
| Addresses in the largest entity | 81 |
| Entity map SHA-256 | `7819fda8b61fa9b668acbf50de000aa0640b22c7d42d57b43dc8b28658bbcac7` |

The hash is SHA-256 of the sorted (address, master) pairs in compact JSON (`herdbench.truth.entities.map_hash`).
Neither the map nor any address is published.

## Market makers (section 5)

Entities passing the loosest share, size and activity tests in any window: 1,687. Market makers under the main rule per snapshot date: median 135, range 65 to 174. Dates without a non_mm cohort (no known set within 15 days): 0.

## Truth panel (sections 6 and 12)

| Item | Value |
|---|---|
| Snapshot dates | 305 (2025-08-28 to 2026-08-18) |
| Markets (eligible on at least one date) | 404 (300) |
| Cohort cells, published cohorts (eligible) | 413,468 (184,938) |
| Eligible cells suppressed for publication | 21.61% |
| Primary market-days (non_mm, primary panel, eligible) | 8,784 |
| Primary market-days suppressed | 0.68% (0.12% of their OI) |
| Published-panel rerun flag (above 5% is "not representative") | representative |

| Cohort | Eligible cells | Suppressed |
|---|---:|---:|
| `all` | 37,190 | 87.6% |
| `contrarian` | 37,005 | 8.2% |
| `non_mm` | 37,190 | 1.2% |
| `top_av` | 36,549 | 3.2% |
| `trend` | 37,004 | 7.6% |

The `all` cohort is mostly withheld by design: `all` minus `non_mm` is the set of market makers, usually fewer than
20 entities, so the complement rule suppresses `all` whenever publishing both would reveal them. The analysis still
uses every cell.

## Gates (section 11), computed before scoring

| Gate | Result | Bar | Passed |
|---|---|---|---|
| Reconciliation | 99.502% of changed notional over 261 pairs (market makers 99.128%, everyone else 99.528%) | 99% | yes |
| Gate 0 | maker = taker within 1% on 100.000% of 88,711 market-days | 99% | yes |
| Kill criterion 1 (reported, not gating) | 21 US single-stock perps | 25 | no |

Snapshot pairs below 99% on their own: 2026-05-23 (33.6%), 2026-07-17 (65.3%).

## Statement

No proxy-versus-truth statistic of any kind (correlation, regression, ranking, plot or table) was computed before
this addendum was committed. Scoring runs `herdbench build score` on the code of the commit that adds this file;
every departure from the registered design is listed, with its reason, in `docs/deviations.md`, written before
this addendum.
