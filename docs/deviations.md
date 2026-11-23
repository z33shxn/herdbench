# Deviations

Every departure from [the pre-registration](preregistration.md) is listed here with its reason and its effect.
This list was written before any proxy was compared with the truth and is reproduced verbatim in
[results.md](results.md).

1. **The Barone–Lillo appendix was not run.** Section 11 lists their TWAP coefficient (θ = −0.0177, s.e. 0.0010,
   arXiv 2606.15715) as an appendix that gates nothing. Reproducing it means rebuilding both of their metaorder
   definitions (protocol TWAPs and statistical runs) and their regression sample, which is a separate replication
   study rather than a measurement of crowding proxies. It is left out of version 1.0 rather than shipped
   half-verified; the registered parameters stay in `PREREG` for a later release. No result in this release
   depends on it.

2. **Window of `dtl` (days to liquidate).** Section 9 defines dtl as |non_mm net notional| ÷ mean daily volume
   without fixing the window. HerdBench uses the mean daily taker volume over the days of [c − 7d, c) that have
   archived fills (a day missing from the archive is left out of the mean, not counted as zero), and publishes dtl
   for every cohort, each with its own net notional. As registered, dtl is published and never scored against
   `oi_to_volume_7d`.

3. **Which market-days supplied the top-3 holders for the entity map.** Section 4 maps every address that is ever
   one of the three largest holders of an *eligible* market (open interest of at least $1M at the previous
   snapshot). The lookup list was built from markets with at least $1M of open interest on the *same* snapshot.
   The two definitions differ only on days when a market crosses $1M, and the map only merges sub-accounts into
   their master, so the worst case is an entity counted as two in such a market. The list is reproduced exactly
   by `herdbench.truth.build.entity_map_targets`.

4. **Seven-day fill windows that touch a day missing from the archive (added sensitivity check).** The archive
   lacks 38 single days of fills between August 2025 and August 2026, so only 109 of the 305 evaluation snapshots
   have a complete seven-day window behind them. The registered code (`proxies/native.py` at the registering
   commit) counts a missing day as a day with no volume and no liquidations, which biases `liq_intensity_7d`
   down and `oi_to_volume_7d` up on the affected market-days; section 9's rule of dropping such windows was
   written for the liquidation target only. The registered version is run and is the main result. Added after
   registration and before any proxy was compared with the truth: the same statistics with both flow proxies kept
   only on complete windows (`results/sensitivity_complete_flows.csv` and the "complete windows" rows of
   `results/liquidation_target.csv`). No confirmatory proxy uses a seven-day fill window.
