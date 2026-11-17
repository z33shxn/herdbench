"""Build the truth panel: raw snapshots and fills in, disclosure-controlled cohort aggregates out.

Stages (each cached on disk and safe to rerun):

1. ``summarize_fill_days``: one pass over every fill day -> wallet activity (for the market-maker rule),
   liquidated notional and traded volume per market.
2. ``market_panel``: per snapshot date and market -> mark, open interest, long-short identity gap.
3. ``style_panel``: streamed over consecutive snapshot pairs -> daily trend-score contributions.
4. ``truth_cells``: per evaluation date -> cohorts -> cell metrics -> disclosure flags.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from herdbench.config import PREREG, PreRegistration
from herdbench.truth import cohorts, entities, market_aggregates, mm, snapshots, style
from herdbench.truth.metrics import cell_metrics, coheld_flags, entity_positions
from herdbench.truth.privacy import NESTED_PAIRS, apply_complement_rule, apply_disclosure, publish_rounding, published

_FILL = re.compile(r"^fills_(\d{4})-(\d{2})-(\d{2})\.parquet$")
FILL_READ = ["coin", "asset_class", "price", "size", "side", "address", "crossed", "is_liquidation", "timestamp"]
KINDS = ("activity", "liquidations", "volume", "hourly", "liq_hourly")


def _atomic_parquet(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def fill_files(raw: Path) -> dict[date, Path]:
    out = {}
    for f in sorted((Path(raw) / "fills_slim").glob("fills_*.parquet")):
        m = _FILL.match(f.name)
        if m:
            out[date(int(m.group(1)), int(m.group(2)), int(m.group(3)))] = f
    return out


def summarize_fill_days(raw: Path, derived: Path) -> list[date]:
    """Stage 1. Returns the fill days available (the market-maker rule needs to know the gaps)."""
    days = fill_files(raw)
    for day, path in days.items():
        outs = {k: Path(derived) / "fills_daily" / f"{k}_{day:%Y-%m-%d}.parquet" for k in KINDS}
        todo = [k for k, p in outs.items() if not p.exists()]
        if not todo:
            continue
        f = pl.read_parquet(path, columns=FILL_READ)
        makers = {"activity": lambda: mm.daily_activity(f, day),
                  "liquidations": lambda: market_aggregates.daily_liquidations(f, day),
                  "volume": lambda: market_aggregates.daily_volume(f, day),
                  "hourly": lambda: market_aggregates.hourly_bars(f),
                  "liq_hourly": lambda: market_aggregates.hourly_liquidations(f)}
        for kind in todo:
            _atomic_parquet(makers[kind](), outs[kind])
    return sorted(days)


def load_fill_daily(derived: Path, kind: str) -> pl.DataFrame:
    files = sorted((Path(derived) / "fills_daily").glob(f"{kind}_*.parquet"))
    if not files:
        raise FileNotFoundError(f"no {kind} tables under {derived}; run the fill summary first")
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")


def load_activity(derived: Path) -> pl.DataFrame:
    """Wallet activity with addresses as a categorical column (about 30 bytes per wallet-day in memory)."""
    return load_fill_daily(derived, "activity").with_columns(pl.col("address").cast(pl.Categorical))


def snapshot_index(raw: Path) -> dict[date, Path]:
    return snapshots.local_index(raw)


def market_panel(index: dict[date, Path], derived: Path) -> pl.DataFrame:
    """Stage 2, cached; rebuilt when the set of snapshot files changes."""
    cache, side = Path(derived) / "markets.parquet", Path(derived) / "markets.json"
    files = [str(p) for p in index.values()]
    if cache.exists() and side.exists() and json.loads(side.read_text(encoding="utf-8")) == files:
        return pl.read_parquet(cache)
    parts = []
    for day, path in index.items():
        parts.append(snapshots.market_table(snapshots.read_raw(path))
                     .with_columns(date=pl.lit(day), capture_ms=pl.lit(snapshots.capture_ms(path))))
    panel = pl.concat(parts).select(["date", "market", "dex", "mark", "oi_contracts", "oi_notional",
                                     "long_short_gap", "n_positions", "capture_ms"]).sort(["date", "market"])
    _atomic_parquet(panel, cache)
    side.write_text(json.dumps(files), encoding="utf-8")
    return panel


def style_panel(index: dict[date, Path], markets: pl.DataFrame, derived: Path) -> pl.DataFrame:
    """Stage 3, streamed one consecutive snapshot pair at a time; cached per date."""
    out_dir = Path(derived) / "style"
    have = set(index)
    prev_day, prev = None, None
    parts = []
    for t, path in index.items():
        cache = out_dir / f"contrib_{t:%Y-%m-%d}.parquet"
        t1, t2 = t - timedelta(days=1), t - timedelta(days=2)
        if t1 in have and t2 in have:
            if cache.exists():
                part = pl.read_parquet(cache)
            else:
                cur = snapshots.without_protocol(snapshots.read_raw(path)).select(["user", "market", "size"])
                if prev_day != t1 or prev is None:
                    prev = snapshots.without_protocol(snapshots.read_raw(index[t1])).select(["user", "market", "size"])
                part = style.day_contributions(
                    t, cur, prev,
                    markets.filter(pl.col("date") == t).select(["market", "mark"]),
                    markets.filter(pl.col("date") == t1).select(["market", "mark"]),
                    markets.filter(pl.col("date") == t2).select(["market", "mark"]))
                _atomic_parquet(part, cache)
                prev_day, prev = t, cur
            parts.append(part)
        else:
            prev_day, prev = None, None
    parts = [p for p in parts if p.height]
    if not parts:
        return pl.DataFrame(schema=style.EMPTY_SCHEMA)
    return pl.concat(parts).sort(["date", "user"])


def robustness_rules(prereg: PreRegistration = PREREG) -> dict[str, tuple[float, float]]:
    """Cohort name -> (maker share, turnover multiple) for the main rule and each robustness rule."""
    rules = {"non_mm": (prereg.mm_maker_share, prereg.mm_turnover_multiple)}
    for s in prereg.mm_robustness_maker_shares:
        rules[f"non_mm_s{round(s * 100):03d}"] = (s, prereg.mm_turnover_multiple)
    for t in prereg.mm_robustness_turnover:
        rules[f"non_mm_t{round(t):03d}"] = (prereg.mm_maker_share, t)
    return rules


def candidate_entities(activity_entity: pl.DataFrame, fill_days: Iterable[date], dates: Iterable[date],
                       prereg: PreRegistration = PREREG) -> set[str]:
    """Entities passing the loosest share, size and activity tests in any window (turnover not applied)."""
    loosest = min((prereg.mm_maker_share, *prereg.mm_robustness_maker_shares))
    fill_days = set(fill_days)
    out: set[str] = set()
    for d in dates:
        s = mm.set_for(activity_entity, fill_days, d, maker_share=loosest, window_days=prereg.mm_window_days,
                       min_fill_days=prereg.mm_min_fill_days, min_notional=prereg.mm_min_notional,
                       min_active_days=prereg.mm_min_active_days)
        if s:
            out |= s
    return out


def entity_gross_panel(index: dict[date, Path], emap: dict[str, str], entities_wanted: set[str],
                       derived: Path | None = None) -> pl.DataFrame:
    """Gross position notional per snapshot date for the wanted entities (for the turnover test)."""
    cache = Path(derived) / "entity_gross.parquet" if derived else None
    if cache is not None and cache.exists():
        return pl.read_parquet(cache)
    wanted = sorted(entities_wanted)
    parts = []
    for day, path in index.items():
        raw = entities.attach(snapshots.without_protocol(snapshots.read_raw(path)), emap)
        parts.append(raw.filter(pl.col("entity").is_in(wanted)).group_by("entity")
                     .agg(gross=pl.col("notional").sum()).with_columns(date=pl.lit(day)))
    out = (pl.concat(parts).select(["date", "entity", "gross"]).sort(["date", "entity"]) if parts
           else pl.DataFrame(schema={"date": pl.Date, "entity": pl.String, "gross": pl.Float64}))
    if cache is not None:
        _atomic_parquet(out, cache)
    return out


def market_maker_sets(activity_entity: pl.DataFrame, fill_days: Iterable[date], dates: Iterable[date],
                      gross: pl.DataFrame, snapshot_dates: Iterable[date],
                      prereg: PreRegistration = PREREG) -> dict[str, dict[date, set[str] | None]]:
    """Market-maker set per rule and date, with the last known set carried forward over fill gaps."""
    fill_days, snaps = set(fill_days), sorted(set(snapshot_dates))
    out: dict[str, dict[date, set[str] | None]] = {}
    for name, (share, turnover) in robustness_rules(prereg).items():
        known: dict[date, set[str]] = {}
        per_day: dict[date, set[str] | None] = {}
        for d in sorted(dates):
            s = mm.set_for(activity_entity, fill_days, d, maker_share=share, window_days=prereg.mm_window_days,
                           min_fill_days=prereg.mm_min_fill_days, min_notional=prereg.mm_min_notional,
                           min_active_days=prereg.mm_min_active_days, gross=gross, snapshot_dates=snaps,
                           turnover_multiple=turnover)
            if s is not None:
                known[d] = s
            per_day[d] = s if s is not None else mm.carry_forward(known, d, prereg.mm_carry_forward_days)
        out[name] = per_day
    return out


def lagged_account_values(index: dict[date, Path], emap: dict[str, str], day: date,
                          prereg: PreRegistration = PREREG) -> pl.DataFrame | None:
    """Entity account values from the snapshot 8 days earlier, or the latest one 8 to 14 days earlier."""
    for lag in range(prereg.top_av_lag_days, prereg.top_av_lag_max_days + 1):
        src = day - timedelta(days=lag)
        if src in index:
            return snapshots.entity_account_values(snapshots.without_protocol(snapshots.read_raw(index[src])), emap)
    return None


def _diffs(assigned: pl.DataFrame, day: date) -> pl.DataFrame:
    rows = []
    for parent, child in NESTED_PAIRS:
        d = assigned.filter(pl.col(parent) & ~pl.col(child))
        if d.height == 0:
            continue
        g = (d.sort(["market", "notional"], descending=[False, True])
             .group_by("market", maintain_order=True)
             .agg(n_entities=pl.len().cast(pl.Int64), top2=pl.col("notional").head(2).sum(),
                  gross=pl.col("notional").sum()))
        rows.append(g.with_columns(date=pl.lit(day), parent=pl.lit(parent), child=pl.lit(child),
                                   top2_share=pl.col("top2") / pl.col("gross"))
                    .select(["date", "market", "parent", "child", "n_entities", "top2_share"]))
    if not rows:
        return pl.DataFrame(schema={"date": pl.Date, "market": pl.String, "parent": pl.String, "child": pl.String,
                                    "n_entities": pl.Int64, "top2_share": pl.Float64})
    return pl.concat(rows)


def truth_cells(index: dict[date, Path], markets: pl.DataFrame, mm_sets: dict[str, dict[date, set[str] | None]],
                contrib_entity: pl.DataFrame, emap: dict[str, str], dates: Iterable[date],
                prereg: PreRegistration = PREREG) -> pl.DataFrame:
    """Stage 4: every cohort cell for every requested snapshot date, with publication flags.

    ``suppressed`` marks cells that fail the cell or the complement rule; robustness cohorts are kept for
    the analysis but are never publishable. ``eligible`` uses open interest at the previous day's snapshot.
    """
    names = list(prereg.cohorts) + list(prereg.robustness_cohorts)
    parts, diff_parts = [], []
    for day in dates:
        if day not in index:
            continue
        mk = markets.filter(pl.col("date") == day)
        prev = markets.filter(pl.col("date") == day - timedelta(days=1))
        eligible = set(prev.filter(pl.col("oi_notional") >= prereg.min_oi_notional)["market"].to_list())
        raw = snapshots.without_protocol(snapshots.read_raw(index[day]))
        pos = entities.attach(raw, emap).join(mk.select(["market", "mark"]), on="market", how="inner")
        held = coheld_flags(entity_positions(pos, fragile_distance=prereg.fragile_distance), eligible,
                            prereg.coheld_min_other)
        gross_all = held.group_by("entity").agg(_g=pl.col("gross_addr").sum())
        lev = (gross_all.join(snapshots.entity_account_values(raw, emap), on="entity", how="left")
               .with_columns(w=pl.when(pl.col("av") > 0)
                             .then(pl.min_horizontal(pl.col("_g") / pl.col("av"), pl.lit(prereg.lwi_leverage_cap)))
                             .otherwise(pl.lit(prereg.lwi_leverage_cap)))
               .select(["entity", "w"]))
        held = held.join(lev, on="entity", how="left")
        scores = style.trend_scores(contrib_entity, day, prereg.style_window_days, prereg.style_min_flows, key="entity")
        robust = {n: mm_sets[n].get(day) for n in prereg.robustness_cohorts if n in mm_sets}
        assigned = cohorts.assign(held, mm_sets["non_mm"].get(day), robust, scores,
                                  lagged_account_values(index, emap, day, prereg),
                                  top_fraction=prereg.top_av_fraction, threshold=prereg.trend_threshold)
        stacked = [assigned.filter(pl.col(c)).with_columns(cohort=pl.lit(c)) for c in names]
        stacked = [x for x in stacked if x.height]
        if not stacked:
            continue
        cells = cell_metrics(pl.concat(stacked).with_columns(date=pl.lit(day)), top_share=prereg.top_share_fraction)
        cells = (cells.join(mk.select(["market", "dex", "oi_notional"]), on="market", how="left")
                 .join(prev.select(["market", pl.col("oi_notional").alias("oi_prev")]), on="market", how="left")
                 .with_columns(eligible=pl.col("market").is_in(sorted(eligible))))
        parts.append(cells)
        diff_parts.append(_diffs(assigned, day))
    if not parts:
        raise ValueError("no truth cells built for the requested dates")
    out = apply_disclosure(pl.concat(parts), prereg.min_entities, prereg.dominance_top2_max)
    out = apply_complement_rule(out, pl.concat(diff_parts), prereg.min_entities, prereg.dominance_top2_max)
    out = out.with_columns(suppressed=pl.col("suppressed") | pl.col("cohort").is_in(list(prereg.robustness_cohorts)))
    return out.sort(["date", "market", "cohort"])


PUBLISHED_COLUMNS = ["date", "market", "dex", "cohort", "n_entities", "gross_notional", "net_notional", "ni", "ai",
                     "lwi", "abs_ni", "top10_share", "fragile_share", "coheld_share", "oi_notional", "eligible"]


def publishable(cells: pl.DataFrame) -> pl.DataFrame:
    """The public truth panel: publishable cells only, rounded, no internal columns, no addresses."""
    return publish_rounding(published(cells).select(PUBLISHED_COLUMNS))
