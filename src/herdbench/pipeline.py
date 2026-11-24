"""End-to-end pipeline: cached stages from the raw downloads to the published results.

    inputs -> entities -> truth -> proxies -> publish -> score -> gates -> manifest

Each stage reads the previous stage's output from ``derived/`` and writes its own, so any stage can be
rerun alone. Only aggregates that pass disclosure control are written to ``results/``:

* ``results/``            the truth panel, gates, manifest and every statistic built from Hyperliquid data
                          only (Hyperliquid archive and Ironflow funding, CC BY 4.0)
* ``results/binance/``    statistics that involve Binance Vision data (CC BY-NC-SA 4.0, shared alike),
                          including the primary family, whose common support and ranks depend on them
* ``results/finra/``      statistics from FINRA short interest (FINRA named as source and owner;
                          non-commercial use)

Nothing under ``derived/`` is published: it holds addresses and the entity map.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

from herdbench import __version__, universe
from herdbench.config import PREREG, PreRegistration, data_dir, repo_root, results_dir
from herdbench.fetch import months_between
from herdbench.gates import criteria
from herdbench.grading import bridges, liquidation, power, scorecard, stats, transfer
from herdbench.proxies import binance_px, equity, native
from herdbench.sources import binance, finra, hyperliquid, ironflow
from herdbench.truth import build, entities, mm, style

LICENCE_NATIVE = "CC BY 4.0"
LICENCE_BINANCE = "CC BY-NC-SA 4.0"
BINANCE_NOTE = (
    "Contains statistics derived from Binance Vision data (https://data.binance.vision), "
    "licensed CC BY-NC-SA 4.0; shared under the same licence."
)
FINRA_NOTE = "Source and owner of the short-interest data: FINRA. Summary statistics only; non-commercial use."
IN_SAMPLE_LABEL = "in-sample, published panel"


# ------------------------------------------------------------------------------------------ paths
@dataclass(frozen=True)
class Paths:
    """``raw``: downloads; ``derived``: private intermediate tables; ``results``: published aggregates."""

    raw: Path
    derived: Path
    results: Path

    @classmethod
    def default(cls) -> Paths:
        d = data_dir()
        return cls(d / "raw", d / "derived", results_dir())

    @classmethod
    def under(cls, data: Path, results: Path | None = None) -> Paths:
        data = Path(data)
        return cls(data / "raw", data / "derived", Path(results) if results else data / "results")

    @property
    def hf(self) -> Path:
        return self.raw / "hf"

    @property
    def binance_results(self) -> Path:
        return self.results / "binance"

    @property
    def finra_results(self) -> Path:
        return self.results / "finra"


def eval_dates(index: Iterable[date], prereg: PreRegistration = PREREG) -> list[date]:
    lo, hi = date.fromisoformat(prereg.evaluation_start), date.fromisoformat(prereg.evaluation_end)
    return [d for d in sorted(index) if lo <= d <= hi]


# ------------------------------------------------------------------------------------------ writing
def jsonable(obj):
    """Plain JSON types from frames, numpy scalars, dates and NaN (written as null)."""
    if obj is None or obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, pd.DataFrame):
        return [jsonable(r) for r in obj.to_dict(orient="records")]
    if isinstance(obj, pd.Series):
        return jsonable(obj.to_dict())
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in (sorted(obj) if isinstance(obj, set) else obj)]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return obj.as_posix()
    return obj


def write_text(path: Path, text: str) -> None:
    """UTF-8 with LF line ends on every platform, so published hashes do not depend on the OS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_json(path: Path, payload) -> None:
    write_text(path, json.dumps(jsonable(payload), indent=1, allow_nan=False) + "\n")


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, float_format="%.6g", lineterminator="\n")


def _is_binance(name: str) -> bool:
    return name.startswith("bn_")


def write_split(df: pd.DataFrame, paths: Paths, name: str, column: str = "proxy", label: str | None = None) -> None:
    """Rows built from Binance proxies go to ``results/binance``; the rest to ``results/``."""
    if df is None or df.empty:
        return
    if label:
        df = df.assign(label=label)
    bn = df[column].map(_is_binance)
    if (~bn).any():
        write_csv(df[~bn].assign(licence=LICENCE_NATIVE), paths.results / f"{name}.csv")
    if bn.any():
        write_csv(df[bn].assign(licence=LICENCE_BINANCE), paths.binance_results / f"{name}.csv")


# ------------------------------------------------------------------------------------------ stage 1-2
@dataclass
class Inputs:
    index: dict[date, Path]
    markets: pl.DataFrame
    fill_days: list[date]


def stage_inputs(paths: Paths) -> Inputs:
    """Daily fill summaries and the per-market snapshot panel (both cached)."""
    fill_days = build.summarize_fill_days(paths.hf, paths.derived)
    index = build.snapshot_index(paths.hf)
    if not index:
        raise FileNotFoundError(f"no snapshots under {paths.hf / 'perp_archive'}; run `herdbench fetch` first")
    return Inputs(index, build.market_panel(index, paths.derived), fill_days)


# ------------------------------------------------------------------------------------------ entities
def stage_entities(paths: Paths, prereg: PreRegistration = PREREG, api=None) -> tuple[dict[str, str], dict]:
    """Find the addresses to map, look up their roles and sub-accounts (resumable, hours), build the map."""
    inp = stage_inputs(paths)
    target_file = paths.derived / "entity_map_targets.parquet"
    if target_file.exists():
        targets = pl.read_parquet(target_file)
    else:
        targets = build.entity_map_targets(
            inp.index, inp.markets, build.load_activity(paths.derived), inp.fill_days, prereg
        )
        build._atomic_parquet(targets, target_file)
    entities.fetch_roles(
        targets["address"].to_list(), api or hyperliquid.PacedInfo(), paths.derived / "entity_roles.jsonl"
    )
    return load_entity_map(paths)


def load_entity_map(paths: Paths) -> tuple[dict[str, str], dict]:
    """The entity map and a summary of it (counts and hash only; no addresses)."""
    store = paths.derived / "entity_roles.jsonl"
    if not store.exists():
        raise FileNotFoundError(f"{store} not found; run `herdbench build entities` first")
    roles, subs = entities.load_store(store)
    emap = entities.build_map(roles, subs)
    sizes = Counter(emap.values())
    target_file = paths.derived / "entity_map_targets.parquet"
    targets = set(pl.read_parquet(target_file)["address"].to_list()) if target_file.exists() else set(roles)
    masters = {m for r, m in roles.values() if r == "subAccount" and m} | {
        a for a, (r, _m) in roles.items() if r == "user"
    }
    role_counts = Counter(r for r, _m in roles.values())
    info = {
        "targets": len(targets),
        "roles_answered": len(roles),
        "roles_missing": len(targets - set(roles)),
        "role_counts": dict(sorted(role_counts.items())),
        "masters_to_list": len(masters),
        "masters_listed": len(masters & set(subs)),
        "addresses_mapped": len(emap),
        "entities_with_several_addresses": sum(1 for n in sizes.values() if n > 1),
        "largest_entity_addresses": max(sizes.values(), default=0),
        "map_sha256": entities.map_hash(emap),
    }
    info["complete"] = info["roles_missing"] == 0 and info["masters_listed"] == info["masters_to_list"]
    return emap, info


# ------------------------------------------------------------------------------------------ truth
def stage_truth(
    paths: Paths,
    prereg: PreRegistration = PREREG,
    dates: Sequence[date] | None = None,
    require_complete_map: bool = True,
) -> pl.DataFrame:
    """Every cohort cell for every evaluation date, with disclosure flags (``derived/truth_cells.parquet``)."""
    emap, info = load_entity_map(paths)
    if require_complete_map and not info["complete"]:
        raise RuntimeError(f"the entity map is incomplete ({info}); finish `herdbench build entities` first")
    inp = stage_inputs(paths)
    dates = list(dates) if dates is not None else eval_dates(inp.index, prereg)
    contrib = style.entity_contributions(build.style_panel(inp.index, inp.markets, paths.derived), emap)
    activity = mm.entity_activity(build.load_activity(paths.derived), emap)
    # An entity that never trades the minimum notional over the whole archive cannot pass in any window.
    totals = activity.group_by("entity").agg(t=(pl.col("maker_ntl") + pl.col("taker_ntl")).sum())
    active = totals.filter(pl.col("t") >= prereg.mm_min_notional)["entity"].to_list()
    activity = activity.filter(pl.col("entity").is_in(active))
    cands = build.candidate_entities(activity, inp.fill_days, dates, prereg)
    gross = build.entity_gross_panel(inp.index, emap, cands, paths.derived)
    sets = build.market_maker_sets(activity, inp.fill_days, dates, gross, inp.index.keys(), prereg)
    cells = build.truth_cells(inp.index, inp.markets, sets, contrib, emap, dates, prereg)
    build._atomic_parquet(cells, paths.derived / "truth_cells.parquet")
    write_json(
        paths.derived / "mm_sets.json",
        {
            rule: {d.isoformat(): (sorted(s) if s is not None else None) for d, s in per_day.items()}
            for rule, per_day in sets.items()
        },
    )
    write_json(
        paths.derived / "truth_info.json",
        {
            "entity_map": info,
            "candidates": len(cands),
            "market_makers": {
                rule: {d.isoformat(): (len(s) if s is not None else None) for d, s in per_day.items()}
                for rule, per_day in sets.items()
            },
        },
    )
    return cells


def load_mm_sets(paths: Paths) -> dict[str, dict[date, set[str] | None]]:
    raw = json.loads((paths.derived / "mm_sets.json").read_text(encoding="utf-8"))
    return {
        rule: {date.fromisoformat(d): (set(v) if v is not None else None) for d, v in per_day.items()}
        for rule, per_day in raw.items()
    }


PANEL_FILE = Path(__file__).parent / "data" / "primary_panel.json"


def primary_panel(paths: Paths) -> dict:
    """The frozen primary panel: ``derived/primary_panel.json`` if present, else the copy bundled with the package."""
    local = paths.derived / "primary_panel.json"
    return json.loads((local if local.exists() else PANEL_FILE).read_text(encoding="utf-8"))


def select_primary_panel(
    markets: pl.DataFrame, prereg: PreRegistration = PREREG, listed: Callable[[str], bool] | None = None
) -> dict:
    """The 30 largest main-dex markets by open interest on the selection snapshot with a Binance USDT perp.

    ``listed(symbol)`` says whether Binance lists the perpetual; by default, whether Vision has its daily
    metrics file for the selection date.
    """
    day = date.fromisoformat(prereg.binance_selection_snapshot)
    if listed is None:
        vision = binance.Vision()
        listed = lambda sym: vision.metrics(sym, day) is not None  # noqa: E731
    ranked = markets.filter((pl.col("date") == day) & (pl.col("dex") == "main")).sort("oi_notional", descending=True)
    chosen: dict[str, str] = {}
    skipped: list[str] = []
    for m in ranked["market"].to_list():
        sym = binance.binance_symbol(m)
        if listed(sym):
            chosen[m] = sym
        else:
            skipped.append(m)
        if len(chosen) == prereg.binance_markets:
            break
    return {
        "selection_snapshot": prereg.binance_selection_snapshot,
        "markets": list(chosen),
        "binance_symbols": chosen,
        "skipped": skipped,
    }


def truth_summary(cells: pl.DataFrame, panel_markets: Sequence[str], prereg: PreRegistration = PREREG) -> dict:
    """Coverage and suppression, recorded in the pre-analysis addendum before any proxy is scored."""
    publishable_cohorts = list(prereg.cohorts)
    c = cells.filter(pl.col("cohort").is_in(publishable_cohorts))
    elig = c.filter(pl.col("eligible"))
    prim = elig.filter((pl.col("cohort") == prereg.primary_cohort) & pl.col("market").is_in(list(panel_markets)))

    def share(frame: pl.DataFrame) -> float:
        return float(frame.select(pl.col("suppressed").mean()).item()) if frame.height else float("nan")

    def oi_share(frame: pl.DataFrame) -> float:
        total = float(frame.select(pl.col("oi_notional").sum()).item()) if frame.height else 0.0
        hidden = float(frame.filter(pl.col("suppressed")).select(pl.col("oi_notional").sum()).item())
        return hidden / total if total else float("nan")

    per_cohort = {
        k: {"cells": g.height, "suppressed_share": share(g)}
        for (k,), g in elig.group_by(["cohort"], maintain_order=True)
    }
    return {
        "dates": c["date"].n_unique(),
        "first_date": c["date"].min(),
        "last_date": c["date"].max(),
        "markets": c["market"].n_unique(),
        "eligible_markets": elig["market"].n_unique(),
        "cells": c.height,
        "eligible_cells": elig.height,
        "eligible_suppressed_share": share(elig),
        "per_cohort": dict(sorted(per_cohort.items())),
        "primary_market_days": prim.height,
        "primary_suppressed_share": share(prim),
        "primary_suppressed_oi_share": oi_share(prim),
        "published_panel_flag": (
            "not representative" if prim.height and share(prim) > prereg.suppressed_flag_share else "representative"
        ),
    }


# ------------------------------------------------------------------------------------------ proxies
def load_funding(paths: Paths, start: date, end: date) -> pl.DataFrame:
    """Hourly funding and premium from the local Ironflow files; analysis never downloads anything."""
    months = months_between(start, end)
    root = paths.raw / "ironflow" / "hyperliquid-funding"
    missing = [m for m in months if not (root / f"{m}.parquet").exists()]
    if missing:
        raise FileNotFoundError(f"funding months {missing} are not under {root}; run `herdbench fetch` first")
    src = ironflow.Ironflow("hyperliquid-funding", revision=ironflow.FUNDING_REVISION, cache_dir=paths.raw / "ironflow")
    return src.funding(months)


def _read_zips(files: Iterable[Path], parser) -> pd.DataFrame:
    frames = []
    for path in files:
        with zipfile.ZipFile(path) as z:
            frames.append(parser(z.read(z.namelist()[0]).decode("utf-8")))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


BINANCE_LAYOUT = {
    "metrics": ("daily/metrics/{s}", binance.parse_metrics),
    "klines": ("monthly/klines/{s}/5m", binance.parse_klines),
    "funding": ("monthly/fundingRate/{s}", binance.parse_funding),
}


def load_binance(paths: Paths, symbols: Sequence[str]) -> dict[str, pl.DataFrame]:
    """Vision metrics, 5-minute klines and funding for ``symbols``, parsed once and cached."""
    root = paths.raw / "binance" / "futures" / "um"
    cache = paths.derived / "binance"
    out = {}
    for kind, (sub, parser) in BINANCE_LAYOUT.items():
        files = {s: sorted((root / sub.format(s=s)).glob("*.zip")) for s in symbols}
        key = hashlib.sha256(json.dumps({s: [f.name for f in fs] for s, fs in files.items()}).encode()).hexdigest()
        f, side = cache / f"{kind}.parquet", cache / f"{kind}.json"
        if f.exists() and side.exists() and json.loads(side.read_text(encoding="utf-8")).get("key") == key:
            out[kind] = pl.read_parquet(f)
            continue
        frames = []
        for s, fs in files.items():
            df = _read_zips(fs, parser)
            if len(df):
                frames.append(df.assign(symbol=s))
        if not frames:
            raise FileNotFoundError(f"no Binance {kind} files under {root}; run `herdbench fetch` first")
        table = pl.from_pandas(pd.concat(frames, ignore_index=True))
        build._atomic_parquet(table, f)
        side.write_text(json.dumps({"key": key}), encoding="utf-8")
        out[kind] = table
    return out


def _cutoffs(markets: pl.DataFrame) -> pl.DataFrame:
    """date -> capture time and proxy cutoff (the last full UTC hour at or before the capture)."""
    caps = markets.select(["date", "capture_ms"]).unique().sort("date")
    return caps.with_columns(
        capture=pl.col("capture_ms").map_elements(
            lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc), return_dtype=pl.Datetime("us", "UTC")
        ),
        cutoff=pl.col("capture_ms").map_elements(native.cutoff, return_dtype=pl.Datetime("us", "UTC")),
    )


def oi_timing(cutoffs: pl.DataFrame, dates: Sequence[date]) -> pl.DataFrame:
    """date -> whether the snapshots one and eight days earlier were captured before this snapshot's cutoff."""
    caps = cutoffs.select(["date", "capture"])
    lagged = [
        caps.rename({"date": "d", "capture": f"cap{k}"}).with_columns(date=pl.col("d") + pl.duration(days=k)).drop("d")
        for k in (1, 8)
    ]
    g = pl.DataFrame({"date": list(dates)}, schema={"date": pl.Date}).join(
        cutoffs.select(["date", "cutoff"]), on="date", how="left"
    )
    for lag in lagged:
        g = g.join(lag, on="date", how="left")
    ok = (pl.col("cap1").is_null() | (pl.col("cap1") < pl.col("cutoff"))) & (
        pl.col("cap8").is_null() | (pl.col("cap8") < pl.col("cutoff"))
    )
    return g.select(["date", ok.alias("oi_timing_ok")])


def stage_proxies(paths: Paths, prereg: PreRegistration = PREREG) -> pl.DataFrame:
    """Every proxy for every main-dex and xyz market-day of the evaluation window (``derived/proxies.parquet``)."""
    inp = stage_inputs(paths)
    dates = eval_dates(inp.index, prereg)
    cut = _cutoffs(inp.markets)
    base = (
        inp.markets.filter(pl.col("date").is_in(dates) & pl.col("dex").is_in(["main", "xyz"]))
        .select(["date", "market", "dex"])
        .join(cut.select(["date", "cutoff"]), on="date", how="left")
    )
    q = base.select(["market", "cutoff"]).unique()
    hourly = build.load_fill_daily(paths.derived, "hourly")
    liq_hourly = build.load_fill_daily(paths.derived, "liq_hourly")
    lo, hi = min(dates) - timedelta(days=8), max(dates)
    funding = load_funding(paths, lo, hi)

    oi_cols = ("oi_prev_contracts", "oi_prev_notional", "oi_change_7d")
    out = (
        base.join(native.funding_features(funding, q), on=["market", "cutoff"], how="left")
        .join(native.price_features(hourly, q), on=["market", "cutoff"], how="left")
        .join(native.open_interest_features(inp.markets, dates), on=["date", "market"], how="left")
        .join(oi_timing(cut, dates), on="date", how="left")
        # open interest must come from snapshots captured before this snapshot's cutoff
        .with_columns([pl.when(pl.col("oi_timing_ok")).then(pl.col(c)).alias(c) for c in oi_cols])
        .drop("oi_timing_ok")
        .with_columns(oi_flow_7d=pl.col("momentum_7d").sign() * pl.col("oi_change_7d"))
    )
    flows = native.flow_features(
        liq_hourly, hourly, out.select(["market", "cutoff", "oi_prev_notional"]).unique(), fill_days=inp.fill_days
    )
    out = out.join(flows, on=["market", "cutoff"], how="left")
    groups = dict.fromkeys(base.filter(pl.col("dex") == "main")["market"].unique().to_list(), "crypto")
    groups.update(dict.fromkeys(universe.stock_markets(), "stocks"))
    como = native.comovement(
        hourly,
        q.filter(pl.col("market").is_in(list(groups))),
        groups,
        prereg.comovement_window,
        prereg.comovement_min_returns,
    )
    out = out.join(como, on=["market", "cutoff"], how="left")

    sym = primary_panel(paths)["binance_symbols"]
    bn = load_binance(paths, list(sym.values()))
    bq = (
        base.filter(pl.col("market").is_in(list(sym)))
        .with_columns(symbol=pl.col("market").replace_strict(sym, return_dtype=pl.String))
        .select(["market", "symbol", "cutoff"])
        .unique()
    )
    out = (
        out.join(
            binance_px.ratio_features(bn["metrics"], bq, prereg.binance_max_staleness_minutes),
            on=["market", "cutoff"],
            how="left",
        )
        .join(binance_px.taker_features(bn["klines"], bq), on=["market", "cutoff"], how="left")
        .join(binance_px.funding_features(bn["funding"], bq), on=["market", "cutoff"], how="left")
    )
    out = out.sort(["date", "market"])
    build._atomic_parquet(out, paths.derived / "proxies.parquet")
    return out


# ------------------------------------------------------------------------------------------ publish
#: Hyperliquid-native proxies published with the panel (Binance-derived proxies are never redistributed).
NATIVE_PROXY_COLUMNS = (
    "date",
    "cutoff",
    "market",
    "dex",
    "funding_24h",
    "funding_7d",
    "pinned_share_24h",
    "pinned_share_7d",
    "premium_24h",
    "abs_premium_24h",
    "momentum_7d",
    "rv_7d",
    "oi_prev_notional",
    "oi_change_7d",
    "oi_flow_7d",
    "liq_intensity_7d",
    "liq_long_7d",
    "liq_short_7d",
    "volume_7d_mean",
    "oi_to_volume_7d",
    "flow_coverage_7d",
    "flows_complete_7d",
    "volume_7d_archived_mean",
    "comovement_30d",
)


def with_dtl(cells: pl.DataFrame, proxies: pl.DataFrame, cutoffs: pl.DataFrame) -> pl.DataFrame:
    """Add each snapshot's proxy cutoff and days-to-liquidate (dtl): |cohort net notional| divided by the
    market's mean daily taker volume over the archived days of [c - 7d, c). dtl is published, never scored
    (section 9; the window is fixed in docs/deviations.md)."""
    vol = "volume_7d_archived_mean" if "volume_7d_archived_mean" in proxies.columns else "volume_7d_mean"
    out = (
        cells.drop([c for c in ("cutoff", "dtl") if c in cells.columns])
        .join(cutoffs.select(["date", "cutoff"]), on="date", how="left")
        .join(proxies.select(["date", "market", pl.col(vol).alias("_vol")]), on=["date", "market"], how="left")
    )
    return out.with_columns(dtl=pl.when(pl.col("_vol") > 0).then(pl.col("net_notional").abs() / pl.col("_vol"))).drop(
        "_vol"
    )


def stage_publish(paths: Paths, prereg: PreRegistration = PREREG, ship: bool = False) -> pl.DataFrame:
    """The public truth panel (``results/truth_panel.parquet``); ``ship`` also copies it into the package."""
    cells = pl.read_parquet(paths.derived / "truth_cells.parquet")
    proxies = pl.read_parquet(paths.derived / "proxies.parquet")
    cutoffs = _cutoffs(pl.read_parquet(paths.derived / "markets.parquet"))
    panel = build.publishable(with_dtl(cells, proxies, cutoffs))
    gates = load_gates(paths)
    label = gates.get("label") if gates else None
    if label:
        panel = panel.with_columns(label=pl.lit(label))
    paths.results.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(
        paths.results / "truth_panel.parquet", compression="zstd", compression_level=19, statistics=False
    )
    _, info = load_entity_map(paths)
    summary = truth_summary(cells, primary_panel(paths)["markets"], prereg)
    summary.update(
        label=label,
        published_rows=panel.height,
        entity_map={
            k: v
            for k, v in info.items()
            if k
            in (
                "targets",
                "addresses_mapped",
                "entities_with_several_addresses",
                "largest_entity_addresses",
                "map_sha256",
            )
        },
    )
    write_json(paths.results / "truth_panel_summary.json", summary)
    native = proxies.select([c for c in NATIVE_PROXY_COLUMNS if c in proxies.columns]).sort(["date", "market"])
    if label:
        native = native.with_columns(label=pl.lit(label))
    native.write_parquet(paths.results / "proxies.parquet", compression="zstd", compression_level=19, statistics=False)
    if ship:
        dest = Path(__file__).parent / "data"
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("truth_panel.parquet", "proxies.parquet"):
            shutil.copyfile(paths.results / name, dest / name)
    return panel


# ------------------------------------------------------------------------------------------ addendum
ADDENDUM = repo_root() / "docs" / "preregistration_addendum.md"
_HASH = re.compile(r"`([0-9a-f]{64})`")


def registered_map_hash(addendum: Path | None = None) -> str | None:
    """The entity-map hash recorded in the pre-analysis addendum, if the addendum exists."""
    addendum = addendum or ADDENDUM
    if not addendum.exists():
        return None
    for line in addendum.read_text(encoding="utf-8").splitlines():
        if "entity map" in line.lower() and "sha-256" in line.lower():
            m = _HASH.search(line)
            if m:
                return m.group(1)
    return None


def addendum_status(paths: Paths, addendum: Path | None = None) -> dict:
    addendum = addendum or ADDENDUM
    registered = registered_map_hash(addendum)
    _, info = load_entity_map(paths)
    return {
        "addendum": addendum.name if addendum.exists() else None,
        "registered_map_sha256": registered,
        "current_map_sha256": info["map_sha256"],
        "matches": registered == info["map_sha256"],
    }


def addendum_markdown(paths: Paths, prereg: PreRegistration = PREREG, recorded_at: str | None = None) -> str:
    """The pre-analysis addendum (pre-registration section 14), rendered from the built truth panel.

    Recorded after the entity map and the truth panel exist and before any proxy is compared with the truth;
    every number comes from ``derived/`` (counts and hashes only, never an address).
    """
    _, info = load_entity_map(paths)
    cells = pl.read_parquet(paths.derived / "truth_cells.parquet")
    panel = primary_panel(paths)["markets"]
    s = truth_summary(cells, panel, prereg)
    t = json.loads((paths.derived / "truth_info.json").read_text(encoding="utf-8"))
    mm_sizes = [n for n in t["market_makers"]["non_mm"].values() if n is not None]
    no_mm = sum(1 for n in t["market_makers"]["non_mm"].values() if n is None)
    when = recorded_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    median = sorted(mm_sizes)[len(mm_sizes) // 2] if mm_sizes else 0
    gates = load_gates(paths)
    gates_text = ""
    if gates:
        rec, g0, k1 = gates["reconciliation"], gates["gate0"], gates["kill_criterion_1"]
        short = (
            ", ".join(
                f"{x['date']} ({100 * x['reconciled_share']:.1f}%)" for x in rec.get("pairs_below_threshold") or []
            )
            or "none"
        )
        gates_text = (
            "\n## Gates (section 11), computed before scoring\n\n"
            "| Gate | Result | Bar | Passed |\n|---|---|---|---|\n"
            f"| Reconciliation | {100 * rec['reconciled_share']:.3f}% of changed notional over {rec['pairs']} pairs "
            f"(market makers {100 * rec['market_maker_share']:.3f}%, everyone else {100 * rec['rest_share']:.3f}%) "
            f"| 99% | {'yes' if rec['passed'] else 'no'} |\n"
            f"| Gate 0 | maker = taker within 1% on {100 * g0['share_balanced']:.3f}% of {g0['market_days']:,} "
            f"market-days | 99% | {'yes' if g0['passed'] else 'no'} |\n"
            f"| Kill criterion 1 (reported, not gating) | {k1['count']} US single-stock perps | 25 | "
            f"{'yes' if k1['passed'] else 'no'} |\n\n"
            f"Snapshot pairs below 99% on their own: {short}.\n"
        )
    mm_text = (
        f"Entities passing the loosest share, size and activity tests in any window: {t['candidates']:,}. "
        f"Market makers under the main rule per snapshot date: median {median}, range "
        f"{min(mm_sizes) if mm_sizes else 0} to {max(mm_sizes) if mm_sizes else 0}. Dates without a non_mm cohort "
        f"(no known set within 15 days): {no_mm}."
    )
    roles = ", ".join(f"{k} {v:,}" for k, v in info["role_counts"].items())
    suppressed = (
        f"{100 * s['primary_suppressed_share']:.2f}% ({100 * s['primary_suppressed_oi_share']:.2f}% of their OI)"
    )
    cohort_rows = chr(10).join(
        f"| `{k}` | {v['cells']:,} | {100 * v['suppressed_share']:.1f}% |" for k, v in s["per_cohort"].items()
    )
    return f"""# Pre-analysis addendum

**Recorded:** {when}, after the entity map and the truth panel were built and **before any proxy-versus-truth
statistic was computed**. Proxies had been computed from their own data; none had been compared with any truth.
This file is committed and timestamped (`docs/timestamps/`) before scoring starts, and the scoring code refuses
to run without it (`herdbench build score --strict` also requires the entity map below, by hash).

## Entity map (section 4)

| Item | Value |
|---|---|
| Addresses looked up (market-maker candidates and top-3 holders) | {info["targets"]:,} |
| Roles answered | {info["roles_answered"]:,} ({roles}) |
| Masters whose sub-account lists were read | {info["masters_listed"]:,} of {info["masters_to_list"]:,} |
| Addresses mapped to a master | {info["addresses_mapped"]:,} |
| Entities with more than one address | {info["entities_with_several_addresses"]:,} |
| Addresses in the largest entity | {info["largest_entity_addresses"]:,} |
| Entity map SHA-256 | `{info["map_sha256"]}` |

The hash is SHA-256 of the sorted (address, master) pairs in compact JSON (`herdbench.truth.entities.map_hash`).
Neither the map nor any address is published.

## Market makers (section 5)

{mm_text}

## Truth panel (sections 6 and 12)

| Item | Value |
|---|---|
| Snapshot dates | {s["dates"]} ({s["first_date"]} to {s["last_date"]}) |
| Markets (eligible on at least one date) | {s["markets"]} ({s["eligible_markets"]}) |
| Cohort cells, published cohorts (eligible) | {s["cells"]:,} ({s["eligible_cells"]:,}) |
| Eligible cells suppressed for publication | {100 * s["eligible_suppressed_share"]:.2f}% |
| Primary market-days (non_mm, primary panel, eligible) | {s["primary_market_days"]:,} |
| Primary market-days suppressed | {suppressed} |
| Published-panel rerun flag (above 5% is "not representative") | {s["published_panel_flag"]} |

| Cohort | Eligible cells | Suppressed |
|---|---:|---:|
{cohort_rows}

The `all` cohort is mostly withheld by design: `all` minus `non_mm` is the set of market makers, usually fewer than
20 entities, so the complement rule suppresses `all` whenever publishing both would reveal them. The analysis still
uses every cell.
{gates_text}
## Statement

No proxy-versus-truth statistic of any kind (correlation, regression, ranking, plot or table) was computed before
this addendum was committed. Scoring runs `herdbench build score` on the code of the commit that adds this file;
every departure from the registered design is listed, with its reason, in `docs/deviations.md`, written before
this addendum.
"""


# ------------------------------------------------------------------------------------------ scoring
DIRECTIONAL_EXCLUDED_FOR_STYLE = ("momentum_7d", "oi_flow_7d")
STYLE_COHORTS = ("trend", "contrarian")
SCORED_COHORTS = ("non_mm", "top_av", "trend", "contrarian")
#: Funding rows report the share of pinned hours of their window (section 7).
PINNED = {"funding_24h": "pinned_share_24h", "funding_7d": "pinned_share_7d", "bn_funding_24h": "bn_pinned_share_24h"}
#: Proxies built from 7-day fill windows; the sensitivity check keeps only complete windows.
FLOW_PROXIES = ("liq_intensity_7d", "oi_to_volume_7d")
#: Cross-venue agreement (section 7): each Binance ratio against the observed quantity it claims to measure.
CROSS_VENUE = (
    ("bn_global_ls", "ai", "all"),
    ("bn_top_account_ls", "ai", "top_av"),
    ("bn_top_position_ls", "ni", "top_av"),
)


def scored_cells(cells: pl.DataFrame) -> pl.DataFrame:
    """Only eligible market-days enter any statistic (open interest at the previous snapshot >= $1M)."""
    return cells.filter(pl.col("eligible"))


def bh_flag(df: pd.DataFrame, q: float = 0.05, column: str = "p_value") -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["bh_flag"] = stats.benjamini_hochberg(df[column].fillna(1.0).tolist(), q)
    return df


def grid(
    truth: pl.DataFrame,
    proxies: pl.DataFrame,
    markets: Sequence[str],
    pairs: Sequence[tuple[str, str, str]],
    prereg: PreRegistration,
    signed: bool,
    panel_name: str,
) -> pd.DataFrame:
    """Secondary statistics for every (proxy, truth metric, cohort) pair; flagged with Benjamini-Hochberg.

    Funding rows also report their share of pinned hours (pre-registration section 7) over the market-days scored.
    """
    rows = []
    for k, (proxy, metric, cohort) in enumerate(pairs):
        if proxy not in proxies.columns:
            continue
        t_cells = truth.filter(pl.col("cohort") == cohort)
        t = scorecard.wide(t_cells, metric, markets)
        p = scorecard.wide(proxies, proxy, markets)
        if t.empty or p.empty:
            continue
        oi = scorecard.wide(t_cells, "oi_notional", markets)
        row = scorecard.score_pair(p, t, oi, prereg, signed=signed, seed_offset=k)
        extra = {}
        if proxy in PINNED and PINNED[proxy] in proxies.columns:
            pin = scorecard.wide(proxies, PINNED[proxy], markets).reindex(index=p.index, columns=p.columns)
            used = (p.notna() & t.reindex(index=p.index, columns=p.columns).notna()).to_numpy()
            vals = pin.to_numpy()[used & ~np.isnan(pin.to_numpy())]
            extra["pinned_share"] = float(vals.mean()) if vals.size else float("nan")
        rows.append({"panel": panel_name, "proxy": proxy, "truth": metric, "cohort": cohort, **row, **extra})
    return bh_flag(pd.DataFrame(rows))


def directional_pairs(proxies: Iterable[str], prereg: PreRegistration) -> list[tuple[str, str, str]]:
    proxies = list(proxies)
    pairs = [
        (p, m, c)
        for p in proxies
        for c in SCORED_COHORTS
        for m in prereg.signed_truths
        if not (p in DIRECTIONAL_EXCLUDED_FOR_STYLE and c in STYLE_COHORTS)
    ]
    return pairs + [(p, "ai", "all") for p in proxies]


def unsigned_pairs(proxies: Iterable[str], prereg: PreRegistration) -> list[tuple[str, str, str]]:
    return [
        (p, m, c)
        for p in proxies
        for m in prereg.unsigned_truths
        for c in SCORED_COHORTS
        if not (p == "comovement_30d" and m != "coheld_share")
    ]


def all_market_panel(cells: pl.DataFrame, prereg: PreRegistration) -> list[str]:
    """Main-dex markets with at least ``min_market_days`` eligible non_mm days (the secondary panel)."""
    c = cells.filter((pl.col("dex") == "main") & (pl.col("cohort") == prereg.primary_cohort))
    counts = c.group_by("market").len()
    return sorted(counts.filter(pl.col("len") >= prereg.min_market_days)["market"].to_list())


def with_panel_comovement(
    proxies: pl.DataFrame, markets: Sequence[str], hourly: pl.DataFrame, prereg: PreRegistration
) -> pl.DataFrame:
    """The proxy table for one panel, with comovement measured against that panel's other markets (section 7)."""
    px = proxies.filter(pl.col("market").is_in(list(markets)))
    q = px.select(["market", "cutoff"]).unique()
    como = native.comovement(
        hourly, q, dict.fromkeys(markets, "panel"), prereg.comovement_window, prereg.comovement_min_returns
    )
    return px.drop("comovement_30d").join(como, on=["market", "cutoff"], how="left")


def complete_flows(proxies: pl.DataFrame) -> pl.DataFrame:
    """Flow proxies kept only where every day of their 7-day window has archived fills (sensitivity check)."""
    return proxies.with_columns([pl.when(pl.col("flows_complete_7d")).then(pl.col(c)).alias(c) for c in FLOW_PROXIES])


def load_gates(paths: Paths) -> dict | None:
    f = paths.results / "gates.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def stage_score(
    paths: Paths, prereg: PreRegistration = PREREG, strict_addendum: bool = False, addendum: Path | None = None
) -> dict:
    """Every pre-registered statistic. Requires the pre-analysis addendum; ``strict_addendum`` also requires
    the entity map to hash to the value it records (a rerun months later may see new sub-accounts).

    Run the gates first (``run_all`` does): if a gate failed, every result file carries the label "fills
    incomplete" and ai(all) becomes the headline check (section 11).
    """
    status = addendum_status(paths, addendum)
    if status["addendum"] is None or (strict_addendum and not status["matches"]):
        raise RuntimeError(f"pre-analysis addendum check failed: {status}")
    gates = load_gates(paths)
    label = gates.get("label") if gates else None
    cells = scored_cells(pl.read_parquet(paths.derived / "truth_cells.parquet"))
    proxies = pl.read_parquet(paths.derived / "proxies.parquet")
    hourly = build.load_fill_daily(paths.derived, "hourly")
    panel = primary_panel(paths)["markets"]
    res: dict = {"addendum": status, "label": label}

    primary = scorecard.primary_family(
        cells, proxies, panel, list(prereg.confirmatory_proxies), prereg, prereg.primary_truth, prereg.primary_cohort
    )
    support = scorecard.support_table(primary["support"])
    # every proxy on the primary panel is scored on the confirmatory family's market-days (section 7)
    px_primary = with_panel_comovement(proxies, panel, hourly, prereg).join(support, on=["date", "market"], how="semi")
    ai_pairs = [(p, "ai", "all") for p in prereg.confirmatory_proxies]
    ai_all = grid(cells, px_primary, primary["markets"], ai_pairs, prereg, True, "primary")
    if not ai_all.empty:
        cols = {"rho": "ai_all_rho", "ci_low": "ai_all_ci_low", "ci_high": "ai_all_ci_high", "p_value": "ai_all_p"}
        picked = ai_all[["proxy", *cols]].rename(columns=cols)
        primary["table"] = primary["table"].merge(picked, on="proxy", how="left")
    res["primary"] = primary
    published = pl.read_parquet(paths.results / "truth_panel.parquet").filter(pl.col("eligible"))
    rerun = scorecard.primary_family(
        published,
        proxies,
        panel,
        list(prereg.confirmatory_proxies),
        prereg,
        prereg.primary_truth,
        prereg.primary_cohort,
    )
    summary = truth_summary(pl.read_parquet(paths.derived / "truth_cells.parquet"), panel, prereg)
    res["published_rerun"] = {
        "table": rerun["table"],
        "n_common_days": rerun["n_common_days"],
        "flag": summary["published_panel_flag"],
        "primary_suppressed_share": summary["primary_suppressed_share"],
    }
    write_primary(paths, res, prereg)

    secondary = all_market_panel(cells, prereg)
    px_all = with_panel_comovement(proxies, secondary, hourly, prereg)
    native_dir = [p for p in (*prereg.confirmatory_proxies, *prereg.secondary_directional) if not _is_binance(p)]
    all_dir = [*prereg.confirmatory_proxies, *prereg.secondary_directional]
    native_unsigned = [p for p in prereg.unsigned_proxies if not _is_binance(p)]
    robust = [(p, prereg.primary_truth, c) for p in prereg.confirmatory_proxies for c in prereg.robustness_cohorts]
    control = [("momentum_7d", "ni", "trend")]
    flows = unsigned_pairs(FLOW_PROXIES, prereg)
    tables = {
        "directional_primary": grid(
            cells, px_primary, panel, directional_pairs(all_dir, prereg), prereg, True, "primary"
        ),
        "directional_all_markets": grid(
            cells, px_all, secondary, directional_pairs(native_dir, prereg), prereg, True, "all_markets"
        ),
        "unsigned_primary": grid(
            cells, px_primary, panel, unsigned_pairs(prereg.unsigned_proxies, prereg), prereg, False, "primary"
        ),
        "unsigned_all_markets": grid(
            cells, px_all, secondary, unsigned_pairs(native_unsigned, prereg), prereg, False, "all_markets"
        ),
        "cross_venue": grid(cells, px_primary, panel, CROSS_VENUE, prereg, True, "primary"),
        "positive_control": pd.concat(
            [
                grid(cells, px_primary, panel, control, prereg, True, "primary"),
                grid(cells, px_all, secondary, control, prereg, True, "all_markets"),
            ],
            ignore_index=True,
        ),
        "robustness_cohorts": grid(cells, px_primary, panel, robust, prereg, True, "primary"),
        "sensitivity_complete_flows": pd.concat(
            [
                grid(cells, complete_flows(px_primary), panel, flows, prereg, False, "primary"),
                grid(cells, complete_flows(px_all), secondary, flows, prereg, False, "all_markets"),
            ],
            ignore_index=True,
        ),
    }
    for name, df in tables.items():
        write_split(df, paths, name, label=label)
    res["tables"] = tables
    res["secondary_markets"] = secondary
    res["liquidation"] = score_liquidations(paths, cells, px_all, secondary, prereg, label)
    res["transfer"] = score_transfer(paths, cells, prereg, label)
    res["bridges"] = score_bridges(paths, cells, prereg, label)
    pc = tables["positive_control"]
    write_json(
        paths.results / "score_summary.json",
        {
            "herdbench_version": __version__,
            "label": label,
            "headline": "ai(all)" if label else f"{prereg.primary_truth}({prereg.primary_cohort})",
            "gates_run": gates is not None,
            "addendum": status,
            "primary_markets": primary["markets"],
            "secondary_markets": secondary,
            "n_common_days": primary["n_common_days"],
            "primary_market_days": int(support.height),
            "block_length": primary["block_length"],
            "positive_control": pc[["panel", "rho", "ci_low", "ci_high"]] if not pc.empty else None,
            "kill_criterion_3": {
                k: {"power": v.get("power"), "passed": (v.get("power") or 0) >= prereg.power_threshold}
                for k, v in res["transfer"].items()
            },
            "kill_criterion_4": primary["stability"],
        },
    )
    return res


def write_primary(paths: Paths, res: dict, prereg: PreRegistration = PREREG) -> None:
    primary = res["primary"]
    table = primary["table"].assign(licence=LICENCE_BINANCE)
    if res.get("label"):
        table = table.assign(label=res["label"])
    write_csv(table, paths.binance_results / "primary_family.csv")
    write_json(
        paths.binance_results / "primary_family.json",
        {
            "note": BINANCE_NOTE,
            "licence": LICENCE_BINANCE,
            "label": res.get("label"),
            "truth": "ni(non_mm)",
            "markets": primary["markets"],
            "n_common_days": primary["n_common_days"],
            "block_length": primary["block_length"],
            "half_split": primary["half_split"],
            "halves": {p: {"first_z": a, "second_z": b} for p, (a, b) in primary["halves"].items()},
            "stability": primary["stability"],
            "outranks": primary["outranks"].to_dict(),
            "dominance_claims": dominance_claims(primary, prereg),
            "published_panel_rerun": {**res["published_rerun"], "label": IN_SAMPLE_LABEL},
        },
    )


def dominance_claims(primary: dict, prereg: PreRegistration = PREREG) -> list[dict]:
    """Pairs "A beats B" (A outranks B in at least ``rank_dominance`` of the bootstrap draws), made only when
    kill criterion 4 allows ranking claims (section 8); otherwise no pairwise claim is made."""
    if not primary["stability"].get("ranking_claimed"):
        return []
    out = primary["outranks"]
    return [
        {"winner": a, "loser": b, "share_of_draws": float(out.loc[b, a])}
        for a in out.columns
        for b in out.index
        if a != b and out.loc[b, a] >= prereg.rank_dominance
    ]


def _xs_rows(
    predictors: Sequence[tuple[str, str]],
    frames: dict[str, pl.DataFrame],
    target: pd.DataFrame,
    target_name: str,
    markets: Sequence[str],
    prereg: PreRegistration,
    control: pd.DataFrame | None,
    variant: str,
) -> list[dict]:
    rows = []
    for name, kind in predictors:
        w = scorecard.wide(frames[kind], name, markets)
        if w.empty:
            continue
        raw = liquidation.xs_partial(w, target, None, prereg.min_xs_markets, prereg.nw_lags)
        part = (
            liquidation.xs_partial(w, target, control, prereg.min_xs_markets, prereg.nw_lags)
            if control is not None
            else {"mean": np.nan, "se": np.nan, "p": np.nan}
        )
        rows.append(
            {
                "predictor": name,
                "kind": kind,
                "target": target_name,
                "variant": variant,
                "rho": raw["mean"],
                "se": raw["se"],
                "p_value": raw["p"],
                "n_days": raw["n_days"],
                "partial_rho_rv": part["mean"],
                "partial_se": part["se"],
                "partial_p": part["p"],
            }
        )
    return rows


def score_liquidations(
    paths: Paths,
    cells: pl.DataFrame,
    proxies: pl.DataFrame,
    markets: Sequence[str],
    prereg: PreRegistration,
    label: str | None = None,
) -> dict:
    """Target (b): proxies and observed positioning against liquidations in the next 7 days (native only).

    Predictors, proxies included, enter only on eligible market-days with a non_mm cell, and only snapshots
    of the evaluation window supply outcomes. Rows with ``variant`` "complete windows" are the sensitivity
    check for the flow proxies (see docs/deviations.md); Benjamini-Hochberg covers the registered rows only.
    """
    non_mm = cells.filter(pl.col("cohort") == prereg.primary_cohort)
    keys = non_mm.select(["date", "market"]).unique()
    px = proxies.join(keys, on=["date", "market"], how="semi")
    mk = (
        pl.read_parquet(paths.derived / "markets.parquet")
        .filter(pl.col("market").is_in(list(markets)))
        .join(keys.select("date").unique(), on="date", how="semi")
    )
    fwd = liquidation.forward_liquidations(
        build.load_fill_daily(paths.derived, "liq_hourly"),
        mk,
        list(build.fill_files(paths.hf)),
        prereg.liquidation_horizon_days,
    )
    target_u = scorecard.wide(fwd, "fwd_liq_intensity", markets)
    target_s = scorecard.wide(fwd, "fwd_liq_signed", markets)
    rv = scorecard.wide(px, "rv_7d", markets)
    frames = {"proxy": px, "truth": non_mm}
    unsigned = [(p, "proxy") for p in prereg.unsigned_proxies if not _is_binance(p)] + [
        (m, "truth") for m in prereg.unsigned_truths
    ]
    directional = (*prereg.confirmatory_proxies, *prereg.secondary_directional)
    signed = [(p, "proxy") for p in directional if not _is_binance(p)] + [(m, "truth") for m in prereg.signed_truths]
    rows = _xs_rows(unsigned, frames, target_u, "liquidation intensity", markets, prereg, rv, "registered")
    rows += _xs_rows(signed, frames, target_s, "long minus short liquidations", markets, prereg, None, "registered")
    table = bh_flag(pd.DataFrame(rows))
    flow_predictors = [(p, "proxy") for p in FLOW_PROXIES]
    sens = _xs_rows(
        flow_predictors,
        {"proxy": complete_flows(px)},
        target_u,
        "liquidation intensity",
        markets,
        prereg,
        rv,
        "complete windows",
    )
    candidates = {p: scorecard.wide(px, p, markets) for p in prereg.unsigned_proxies if not _is_binance(p)}
    delta = liquidation.delta_rho(
        scorecard.wide(non_mm, "fragile_share", markets),
        candidates,
        target_u,
        prereg.min_xs_markets,
        prereg.nw_lags,
        prereg.bootstrap_block_min,
        prereg.bootstrap_reps,
        prereg.seed,
    )
    out_table = pd.concat([table, pd.DataFrame(sens)], ignore_index=True).assign(licence=LICENCE_NATIVE)
    if label:
        out_table = out_table.assign(label=label)
    write_csv(out_table, paths.results / "liquidation_target.csv")
    write_json(
        paths.results / "liquidation_delta_rho.json",
        {"licence": LICENCE_NATIVE, "label": label, "markets": len(markets), "comparisons": delta},
    )
    return {"table": out_table, "delta_rho": delta, "n_markets": len(markets), "n_target_rows": fwd.height}


def finra_publication_instant(published_on: date) -> datetime:
    """End of FINRA's publication day in New York (data released that day is public by then)."""
    midnight = datetime.combine(published_on + timedelta(days=1), time(0, 0), tzinfo=equity.NEW_YORK)
    return midnight.astimezone(timezone.utc)


def _utc_ns(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True).astype("datetime64[ns, UTC]")


def transfer_panel(paths: Paths, cells: pl.DataFrame, pairing: str, prereg: PreRegistration = PREREG) -> pd.DataFrame:
    """One row per stock and FINRA settlement date: ni(non_mm) at the paired snapshot and every equity proxy."""
    if pairing not in ("settlement", "publication"):
        raise ValueError("pairing is 'settlement' or 'publication'")
    markets = pl.read_parquet(paths.derived / "markets.parquet")
    captures = _cutoffs(markets)
    stocks = universe.stock_markets()
    lo, hi = date.fromisoformat(prereg.transfer_start), date.fromisoformat(prereg.transfer_end)
    settlements = sorted(d for d in transfer.FINRA_PUBLICATION if lo <= d <= hi)
    truth = cells.filter((pl.col("cohort") == prereg.primary_cohort) & pl.col("market").is_in(stocks))
    ni = {(d, m): v for d, m, v in truth.select(["date", "market", prereg.primary_truth]).iter_rows()}
    src = finra.Finra(cache_dir=paths.raw / "finra")
    rows = []
    for k, s in enumerate(settlements):
        instant = (
            equity.us_close_utc(s)
            if pairing == "settlement"
            else finra_publication_instant(transfer.FINRA_PUBLICATION[s])
        )
        td = equity.first_snapshot_after(captures.select(["date", "capture"]), instant)
        if not (paths.raw / "finra" / f"shrt{s:%Y%m%d}.csv").exists():
            raise FileNotFoundError(f"FINRA short interest for {s} is missing; run `herdbench fetch` first")
        si = src.short_interest(s)
        if td is None or si is None:
            continue
        cut = captures.filter(pl.col("date") == td)["cutoff"][0]
        span = (s - transfer.previous_settlement(s)).days
        si = si.drop_duplicates("symbol", keep="first").set_index("symbol")
        for mk in stocks:
            tick = mk.split(":", 1)[1]
            if (td, mk) not in ni or tick not in si.index:
                continue
            r = si.loc[tick]
            short = r["short_interest"]
            rows.append(
                {
                    "stock": mk,
                    "period": k,
                    "settlement": s,
                    "truth_date": td,
                    "cutoff": cut,
                    "span_days": span,
                    "ni": ni[(td, mk)],
                    "si_change_pct": r["change_pct"],
                    "days_to_cover": r["days_to_cover"],
                    "log_short_interest": (
                        float(np.log(float(short))) if pd.notna(short) and float(short) > 0 else np.nan
                    ),
                }
            )
    panel = pd.DataFrame(rows)
    if panel.empty:
        return panel
    q = (
        pl.from_pandas(panel[["stock", "cutoff", "span_days"]].drop_duplicates())
        .with_columns(pl.col("cutoff").cast(pl.Datetime("us", "UTC")))
        .rename({"stock": "market", "cutoff": "end"})
        .with_columns(start=pl.col("end") - pl.duration(days=pl.col("span_days")))
    )
    first, last = q["start"].min(), q["end"].max()
    assert isinstance(first, datetime)
    assert isinstance(last, datetime)
    funding = load_funding(paths, first.date(), last.date()).filter(pl.col("market").is_in(stocks))
    hourly = build.load_fill_daily(paths.derived, "hourly").filter(pl.col("market").is_in(stocks))
    means = native.period_means(funding, "time", ["funding", "premium"], q.select(["market", "start", "end"]))
    p_end = native.price_at(hourly, q.select(["market", pl.col("end").alias("t")]))
    p_end = p_end.rename({"t": "end", "price": "p_end"})
    p_start = native.price_at(hourly, q.select(["market", pl.col("start").alias("t")]))
    p_start = p_start.rename({"t": "start", "price": "p_start"})
    como = native.comovement(
        hourly,
        q.select(["market", pl.col("end").alias("cutoff")]).unique(),
        dict.fromkeys(stocks, "stocks"),
        prereg.comovement_window,
        prereg.comovement_min_returns,
    )
    feats = (
        q.join(
            means.select(["market", "start", "end", "funding", "premium"]), on=["market", "start", "end"], how="left"
        )
        .join(p_end, on=["market", "end"], how="left")
        .join(p_start, on=["market", "start"], how="left")
        .join(como.rename({"cutoff": "end"}), on=["market", "end"], how="left")
        .with_columns(
            momentum_period=pl.when((pl.col("p_end") > 0) & (pl.col("p_start") > 0)).then(
                (pl.col("p_end") / pl.col("p_start")).log()
            )
        )
        .select(
            [
                "market",
                "end",
                pl.col("funding").alias("funding_period"),
                pl.col("premium").alias("premium_period"),
                "momentum_period",
                "comovement_30d",
            ]
        )
        .unique(["market", "end"])
        .to_pandas()
        .rename(columns={"market": "stock", "end": "cutoff"})
    )
    panel["cutoff"] = _utc_ns(panel["cutoff"])
    feats["cutoff"] = _utc_ns(feats["cutoff"])
    out = panel.merge(feats, on=["stock", "cutoff"], how="left")
    return out.sort_values(["period", "stock"]).reset_index(drop=True)


def score_transfer(paths: Paths, cells: pl.DataFrame, prereg: PreRegistration, label: str | None = None) -> dict:
    """Both pairings, each with kill criterion 3 (clustered power on the panel's actual shape)."""
    out: dict[str, dict] = {}
    expected = {p: (-1 if p in prereg.equity_negative_sign else 1) for p in prereg.equity_proxies}
    for pairing in ("settlement", "publication"):
        panel = transfer_panel(paths, cells, pairing, prereg)
        if panel.empty:
            out[pairing] = {"n_obs": 0, "power": None}
            continue
        stocks = sorted(panel["stock"].unique())
        periods = sorted(panel["period"].unique())
        mask = np.zeros((len(stocks), len(periods)), dtype=bool)
        for st, pe in zip(panel["stock"], panel["period"], strict=True):
            mask[stocks.index(st), periods.index(pe)] = True
        pw = power.clustered_power(mask, prereg.power_rho, prereg.power_ar1, prereg.power_reps, prereg.seed)
        table = transfer.transfer_stats(panel, list(prereg.equity_proxies), expected, pw, prereg.power_threshold)
        written = table.assign(source=FINRA_NOTE)
        if label:
            written = written.assign(label=label)
        write_csv(written, paths.finra_results / f"transfer_{pairing}.csv")
        out[pairing] = {
            "power": pw,
            "power_threshold": prereg.power_threshold,
            "p_values_reported": pw >= prereg.power_threshold,
            "n_stocks": len(stocks),
            "n_periods": len(periods),
            "n_obs": int(mask.sum()),
            "table": table,
        }
    write_json(
        paths.finra_results / "transfer.json",
        {
            "source": FINRA_NOTE,
            "label": label,
            **{k: {kk: vv for kk, vv in v.items() if kk != "table"} for k, v in out.items()},
        },
    )
    return out


def score_bridges(paths: Paths, cells: pl.DataFrame, prereg: PreRegistration, label: str | None = None) -> dict:
    """CFTC positioning against the matching perpetuals, week by week (description only)."""
    files = sorted((paths.raw / "cftc").glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no CFTC series under {paths.raw / 'cftc'}; run `herdbench fetch` first")
    cot = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    cot["market"] = cot["market"].replace({"cme:BTC": "BTC", "cme:ETH": "ETH"})
    captures = _cutoffs(pl.read_parquet(paths.derived / "markets.parquet")).select(["date", "capture"])
    truth = cells.filter(pl.col("cohort") == prereg.primary_cohort).select(["date", "market", prereg.primary_truth])
    weekly, summary = bridges.cot_table(
        truth, cot[["market", "report_date", "net_share"]], captures, prereg.primary_truth
    )
    out = {"weekly_rows": len(weekly), "summary": summary}
    summary = summary.assign(group="managed money / leveraged funds", licence=LICENCE_NATIVE)
    write_csv(summary.assign(label=label) if label else summary, paths.results / "cot_bridges.csv")
    if "asset_net_share" in cot.columns:
        am = cot.dropna(subset=["asset_net_share"])[["market", "report_date", "asset_net_share"]]
        am_summary = bridges.cot_table(
            truth, am.rename(columns={"asset_net_share": "net_share"}), captures, prereg.primary_truth
        )[1]
        am_summary = am_summary.assign(group="asset managers", licence=LICENCE_NATIVE)
        write_csv(
            am_summary.assign(label=label) if label else am_summary,
            paths.results / "cot_bridges_asset_managers.csv",
        )
        out["asset_managers"] = am_summary
    return out


# ------------------------------------------------------------------------------------------ gates
def stage_gates(paths: Paths, prereg: PreRegistration = PREREG) -> dict:
    """Gate 0, the full reconciliation (split by market makers) and kill criterion 1."""
    inp = stage_inputs(paths)
    volume = build.load_fill_daily(paths.derived, "volume")
    g0 = criteria.gate0(volume, prereg.gate0_max_gap, prereg.gate0_min_share)
    k1 = criteria.kc1(volume, universe.stock_markets(), prereg.kc1_min_days, prereg.kc1_min_stocks)
    cache = paths.derived / "reconciliation_pairs.json"
    fills = build.fill_files(paths.hf)
    _, info = load_entity_map(paths)
    key = hashlib.sha256(
        json.dumps(
            {
                "map": info["map_sha256"],
                "mm": sha256_file(paths.derived / "mm_sets.json"),
                "snapshots": [p.name for p in inp.index.values()],
                "fills": [p.name for p in fills.values()],
            }
        ).encode()
    ).hexdigest()
    cached = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    if isinstance(cached, dict) and cached.get("key") == key:
        pairs = cached["pairs"]
    else:
        pairs = criteria.reconcile_pairs(inp.index, fills, market_maker_addresses(paths))
        write_json(cache, {"key": key, "pairs": pairs})
    rec = criteria.reconciliation_summary(pairs, prereg.reconciliation_threshold)
    gates = {
        "gate0": g0,
        "reconciliation": rec,
        "kill_criterion_1": k1,
        "label": None if rec["passed"] and g0["passed"] else "fills incomplete",
    }
    write_json(paths.results / "gates.json", gates)
    return gates


def market_maker_addresses(paths: Paths) -> dict[date, set[str]]:
    """Every address of every non_mm-rule market maker, per snapshot date (for the reconciliation split)."""
    emap, _info = load_entity_map(paths)
    sets = load_mm_sets(paths)["non_mm"]
    members: dict[str, list[str]] = {}
    for addr, ent in emap.items():
        members.setdefault(ent, []).append(addr)
    return {d: {a for e in s for a in members.get(e, [e])} for d, s in sets.items() if s is not None}


# ------------------------------------------------------------------------------------------ input checks
CHECK_MARKETS = ("BTC", "ETH", "SOL", "HYPE", "xyz:NVDA")


def check_funding(
    paths: Paths,
    markets: Sequence[str] = CHECK_MARKETS,
    days: int = 2,
    api: hyperliquid.InfoApi | None = None,
    prereg: PreRegistration = PREREG,
) -> dict:
    """Cross-check the pinned Ironflow funding and premium against Hyperliquid's own ``fundingHistory``.

    One random window of ``days`` days per market (seeded, so the sample is the same for everyone); rows are
    matched by UTC hour and must agree to 1e-12. Needs the network; analysis stages never call it.
    """
    rng = np.random.default_rng(prereg.seed)
    lo, hi = date.fromisoformat(prereg.evaluation_start), date.fromisoformat(prereg.evaluation_end)
    api = api or hyperliquid.InfoApi()
    rows: list[dict] = []
    for m in markets:
        start = lo + timedelta(days=int(rng.integers(0, (hi - lo).days - days)))
        t0 = datetime.combine(start, time(0), tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=days)
        local = (
            load_funding(paths, start, (t1 - timedelta(microseconds=1)).date())
            .filter((pl.col("market") == m) & (pl.col("time") >= t0) & (pl.col("time") < t1))
            .with_columns(hour=pl.col("time").dt.truncate("1h"))
            .select(["hour", pl.col("funding").alias("f_local"), pl.col("premium").alias("p_local")])
        )
        remote_pd = api.funding_history(m, int(t0.timestamp() * 1000), int(t1.timestamp() * 1000) - 1)
        remote = (
            pl.from_pandas(remote_pd)
            .with_columns(pl.col("time").cast(pl.Datetime("us", "UTC")))
            .with_columns(hour=pl.col("time").dt.truncate("1h"))
            .select(["hour", pl.col("funding").alias("f_api"), pl.col("premium").alias("p_api")])
        )
        both = local.join(remote, on="hour", how="inner")
        df = (both["f_local"] - both["f_api"]).abs()
        dp = (both["p_local"] - both["p_api"]).abs()
        rows.append(
            {
                "market": m,
                "window_start": t0,
                "hours_ironflow": local.height,
                "hours_api": remote.height,
                "hours_compared": both.height,
                "hours_equal": int(((df <= 1e-12) & (dp <= 1e-12)).sum()) if both.height else 0,
                "max_abs_diff_funding": float(df.max()) if both.height else float("nan"),
                "max_abs_diff_premium": float(dp.max()) if both.height else float("nan"),
            }
        )
    compared = sum(r["hours_compared"] for r in rows)
    equal = sum(r["hours_equal"] for r in rows)
    out = {
        "dataset": "ironflow/hyperliquid-funding",
        "revision": ironflow.FUNDING_REVISION,
        "reference": "Hyperliquid info API, fundingHistory",
        "hours_compared": compared,
        "hours_equal": equal,
        "share_equal": equal / compared if compared else float("nan"),
        "markets": rows,
    }
    write_json(paths.results / "funding_check.json", out)
    return out


# ------------------------------------------------------------------------------------------ manifest
def environment() -> dict:
    """Versions that can change the bytes of a result (Parquet footers record the polars version)."""
    import platform
    from importlib import metadata

    out: dict[str, str | None] = {"python": platform.python_version()}
    for pkg in ("polars", "pandas", "numpy", "scipy", "pyarrow"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def registered_sha256(doc: Path) -> str | None:
    """SHA-256 of the registered pre-registration, as its timestamp record states it.

    The working-tree copy was retypeset in version 1.1.0 (en dashes and minus signs became hyphens), so its
    own hash differs from the registered one; the manifest records both. Without the record, the file's own
    hash stands in.
    """
    record = doc.parent / "timestamps" / "prereg-v1.txt"
    if record.exists():
        text = record.read_text(encoding="utf-8")
        m = re.search(r"^sha256\(docs/preregistration\.md\) ([0-9a-f]{64})$", text, re.M)
        if m:
            return m.group(1)
    return sha256_file(doc) if doc.exists() else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(paths: Paths, prereg: PreRegistration = PREREG, addendum: Path | None = None) -> dict:
    addendum = addendum or ADDENDUM
    files = {
        f.relative_to(paths.results).as_posix(): sha256_file(f)
        for f in sorted(paths.results.rglob("*"))
        if f.is_file() and f.name != "manifest.json"
    }
    prereg_doc = repo_root() / "docs" / "preregistration.md"
    listing = paths.hf / "listing.json"
    manifest = {
        "herdbench_version": __version__,
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mirror": {
            "repo": "craftify2221/hyperliquid-fills-raw",
            "revision": prereg.mirror_revision,
            "listing_sha256": sha256_file(listing) if listing.exists() else None,
        },
        "funding": {"dataset": "ironflow/hyperliquid-funding", "revision": ironflow.FUNDING_REVISION},
        "environment": environment(),
        "preregistration_sha256": registered_sha256(prereg_doc),
        "preregistration_file_sha256": sha256_file(prereg_doc) if prereg_doc.exists() else None,
        "addendum_sha256": sha256_file(addendum) if addendum.exists() else None,
        "results_sha256": files,
    }
    write_json(paths.results / "manifest.json", manifest)
    return manifest


def run_all(
    paths: Paths, prereg: PreRegistration = PREREG, addendum: Path | None = None, strict_addendum: bool = False
) -> dict:
    """Truth, gates, proxies, publication, scoring and manifest (the entity map must already be complete).

    The gates run right after the truth stage so that a failed gate labels every published file (section 11).
    """
    stage_truth(paths, prereg)
    gates = stage_gates(paths, prereg)  # needs only the truth stage; runs first so every output can carry its label
    stage_proxies(paths, prereg)
    stage_publish(paths, prereg)
    res = stage_score(paths, prereg, strict_addendum=strict_addendum, addendum=addendum)
    res["gates"] = gates
    write_manifest(paths, prereg, addendum)
    return res
