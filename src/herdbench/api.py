"""Bring your own proxy: score any signal against the published truth panel.

    >>> import herdbench
    >>> panel = herdbench.load_truth_panel(cohort="non_mm")
    >>> report = herdbench.grade(my_signal)            # long (date, market, value) or wide (dates x markets)
    >>> print(report)

A signal for snapshot date D must use only information from before that snapshot's ``cutoff`` (a column of
the panel; usually D 00:00 UTC). Every result is labelled "in-sample, published panel": the panel holds only
the cells that passed disclosure control, rounded, and the statistics use the whole published sample. The
pre-registered headline results, computed on every cell before suppression, ship separately under
``results/``. Baselines are the study's own Hyperliquid-native proxies, scored on the same market-days.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

from herdbench.config import PREREG, PreRegistration
from herdbench.grading import scorecard

IN_SAMPLE_LABEL = "in-sample, published panel"
SIGNED_BASELINES = ("premium_24h", "momentum_7d", "oi_flow_7d", "funding_24h", "funding_7d")
UNSIGNED_BASELINES = ("abs_premium_24h", "oi_change_7d", "liq_intensity_7d", "oi_to_volume_7d", "comovement_30d")
TRUTHS = (*PREREG.signed_truths, *PREREG.unsigned_truths, "dtl")
STYLE_COHORTS = ("trend", "contrarian")
STYLE_DEFINING = ("momentum_7d", "oi_flow_7d")
COLUMNS = (
    "signal",
    "z",
    "rho",
    "ci_low",
    "ci_high",
    "p_value",
    "n_markets",
    "n_obs",
    "changes_rho",
    "xs_rho",
    "xs_p",
    "oi_weighted_rho",
    "placebo_rho",
    "fe_beta",
    "fe_se",
    "sign_agreement",
    "sign_low",
    "sign_high",
    "tail_lift",
)


def _data_file(name: str, env: str) -> Path:
    override = os.environ.get(env)
    if override:
        return Path(override).expanduser()
    path = Path(str(resources.files("herdbench") / "data" / name))
    if not path.exists():
        raise FileNotFoundError(
            f"{name} is not bundled with this installation; set {env} to a copy (built by `herdbench build publish`)"
        )
    return path


def panel_path() -> Path:
    """The published truth panel (``HERDBENCH_PANEL`` overrides the bundled copy)."""
    return _data_file("truth_panel.parquet", "HERDBENCH_PANEL")


def proxies_path() -> Path:
    """The study's Hyperliquid-native proxies (``HERDBENCH_PROXIES`` overrides the bundled copy)."""
    return _data_file("proxies.parquet", "HERDBENCH_PROXIES")


@cache
def _read(path: str) -> pl.DataFrame:
    return pl.read_parquet(path)


def load_truth_panel(
    cohort: str | None = None,
    markets: Sequence[str] | None = None,
    eligible_only: bool = False,
    as_pandas: bool = False,
) -> pl.DataFrame | pd.DataFrame:
    """The published truth panel: one row per snapshot date, market and cohort (no addresses, rounded).

    Cohorts: ``all`` (every non-protocol entity), ``non_mm`` (without market makers; the primary crowd),
    ``top_av`` (the largest 20% of non_mm by lagged account value), ``trend`` and ``contrarian``.
    """
    df = _read(str(panel_path()))
    if cohort is not None:
        if cohort not in PREREG.cohorts:
            raise ValueError(f"cohort must be one of {PREREG.cohorts}, not {cohort!r}")
        df = df.filter(pl.col("cohort") == cohort)
    if markets is not None:
        df = df.filter(pl.col("market").is_in(list(markets)))
    if eligible_only:
        df = df.filter(pl.col("eligible"))
    return df.to_pandas() if as_pandas else df


def load_proxies(as_pandas: bool = False) -> pl.DataFrame | pd.DataFrame:
    """Every Hyperliquid-native proxy of the study, per snapshot date and market (computed before each cutoff)."""
    df = _read(str(proxies_path()))
    return df.to_pandas() if as_pandas else df


def _long(signal, value: str | None) -> pd.DataFrame:
    """Any accepted input as a long frame with columns date, market, value (dates not yet interpreted)."""
    if isinstance(signal, pl.DataFrame):
        signal = signal.to_pandas()
    if isinstance(signal, pd.Series):
        if not (isinstance(signal.index, pd.MultiIndex) and signal.index.nlevels == 2):
            raise ValueError("a Series signal needs a (date, market) MultiIndex")
        long = signal.rename("value").reset_index()
        long.columns = ["date", "market", "value"]
        return long
    if isinstance(signal, pd.DataFrame) and {"date", "market"} <= set(signal.columns):
        rest = [c for c in signal.columns if c not in ("date", "market")]
        col = value or (rest[0] if len(rest) == 1 else None)
        if col is None or col not in signal.columns:
            raise ValueError(f"name the value column with value=; candidates: {rest}")
        return signal[["date", "market", col]].rename(columns={col: "value"})
    if isinstance(signal, pd.DataFrame):
        wide = signal.copy()
        wide.index = pd.Index(wide.index, name="date")
        wide.columns = pd.Index([str(c) for c in wide.columns], name="market")
        long = wide.stack(future_stack=True).rename("value").reset_index()
        return long.dropna(subset=["value"])
    raise TypeError("signal must be a pandas or polars DataFrame, or a pandas Series")


def _to_snapshots(long: pd.DataFrame, cutoffs: pl.DataFrame | None) -> tuple[pd.DataFrame, int]:
    """Map each value to a snapshot date. Plain dates are snapshot labels. A timestamp (time zone aware, or with
    a time of day, read as UTC) goes to the first snapshot whose cutoff is at or after it, because a value known
    at time t may only be scored against snapshots cut off at or after t. Returns the frame and the number of
    timestamped values that were aligned."""
    stamps = pd.to_datetime(long["date"])
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_convert("UTC")
    elif bool((stamps != stamps.dt.normalize()).any()):
        stamps = stamps.dt.tz_localize("UTC")
    else:
        return long.assign(date=stamps.dt.normalize()), 0
    if cutoffs is None:
        raise ValueError("a timestamped signal needs the panel's snapshot cutoffs to be aligned")
    cut = cutoffs.select(["date", "cutoff"]).unique().sort("cutoff").to_pandas()
    cut = pd.DataFrame(
        {
            "_snap": pd.to_datetime(cut["date"]),
            "_cut": pd.to_datetime(cut["cutoff"], utc=True).astype("datetime64[ns, UTC]"),
        }
    ).sort_values("_cut")
    frame = long.assign(_t=stamps.astype("datetime64[ns, UTC]")).sort_values("_t")
    merged = pd.merge_asof(frame, cut, left_on="_t", right_on="_cut", direction="forward").dropna(subset=["_snap"])
    merged = merged.sort_values("_t").drop_duplicates(["_snap", "market"], keep="last")
    return merged.assign(date=merged["_snap"])[["date", "market", "value"]], len(frame)


def to_wide(signal, value: str | None = None, cutoffs: pl.DataFrame | None = None) -> pd.DataFrame:
    """Normalise a signal to a snapshot-date x market frame (naive dates; markets as columns).

    Accepts a long table (pandas or polars) with ``date`` and ``market`` columns plus one value column (or
    ``value=``), a pandas Series indexed by (date, market), or a wide pandas frame indexed by date. Plain dates
    are snapshot dates; timestamps are aligned with ``cutoffs`` (the panel's ``date`` and ``cutoff`` columns).
    """
    long, _ = _to_snapshots(_long(signal, value), cutoffs)
    long = long.assign(value=pd.to_numeric(long["value"], errors="raise"))
    if long.duplicated(["date", "market"]).any():
        raise ValueError("the signal has more than one value for some (date, market) pairs")
    return long.pivot(index="date", columns="market", values="value").sort_index().sort_index(axis=1).astype(float)


@dataclass
class GradeReport:
    """Statistics for one signal (first row) and, optionally, the study's baselines on the same market-days."""

    truth: str
    cohort: str
    table: pd.DataFrame
    markets: list[str]
    n_market_days: int
    label: str = IN_SAMPLE_LABEL
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "truth": self.truth,
            "cohort": self.cohort,
            "label": self.label,
            "markets": self.markets,
            "n_market_days": self.n_market_days,
            "notes": self.notes,
            "table": self.table.replace({np.nan: None}).to_dict(orient="records"),
        }

    def __str__(self) -> str:
        head = (
            f"HerdBench grade ({self.label}): {self.truth}({self.cohort}), {len(self.markets)} markets, "
            f"{self.n_market_days} common market-days"
        )
        cols = ["signal", "rho", "ci_low", "ci_high", "p_value", "xs_rho", "sign_agreement", "n_markets"]
        body = self.table[[c for c in cols if c in self.table.columns]].to_string(index=False, float_format="%.3f")
        return "\n".join([head, body, *self.notes])


def grade(
    signal,
    truth: str = "ni",
    cohort: str = "non_mm",
    value: str | None = None,
    markets: Sequence[str] | None = None,
    expected_sign: int = 1,
    baselines: Sequence[str] | bool = True,
    bootstrap_reps: int | None = None,
    prereg: PreRegistration = PREREG,
    panel: pl.DataFrame | None = None,
    proxies: pl.DataFrame | None = None,
) -> GradeReport:
    """Score ``signal`` against ``truth(cohort)`` on the published panel's eligible market-days.

    The main statistic is the within-market Spearman correlation over time, averaged across markets on
    Fisher's z scale, with a circular block-bootstrap interval; the report adds the cross-sectional
    correlation, daily changes, the placebo (truth shifted 60 days), the two-way fixed-effects slope, sign
    agreement and tail lift. ``baselines`` (True for the defaults, a list of proxy names, or False) are
    scored on exactly the same market-days as the signal.
    """
    if truth not in TRUTHS:
        raise ValueError(f"truth must be one of {TRUTHS}, not {truth!r}")
    if cohort not in prereg.cohorts:
        raise ValueError(f"cohort must be one of {prereg.cohorts}, not {cohort!r}")
    if expected_sign not in (1, -1):
        raise ValueError("expected_sign is 1 or -1")
    signed = truth in prereg.signed_truths
    pr = replace(prereg, bootstrap_reps=bootstrap_reps) if bootstrap_reps is not None else prereg
    p = panel if panel is not None else load_truth_panel()
    assert isinstance(p, pl.DataFrame)
    cells = p.filter((pl.col("cohort") == cohort) & pl.col("eligible"))
    notes = []
    if "label" in p.columns and p["label"].drop_nulls().len():
        notes.append(f'the published panel is labelled "{p["label"].drop_nulls()[0]}": a data gate failed')
    long, aligned = _to_snapshots(_long(signal, value), p if "cutoff" in p.columns else None)
    if aligned:
        notes.append(
            f"aligned {aligned} timestamped values to the first snapshot whose cutoff is at or after each "
            "timestamp (the latest value per snapshot and market is kept)"
        )
    user = to_wide(long, "value")
    universe = sorted(set(user.columns) & set(cells["market"].unique().to_list()))
    if markets is not None:
        universe = [m for m in universe if m in set(markets)]
    unknown = sorted(set(user.columns) - set(cells["market"].unique().to_list()))
    if unknown:
        notes.append(
            f"ignored {len(unknown)} markets not in the panel: {', '.join(unknown[:10])}"
            + (" ..." if len(unknown) > 10 else "")
        )
    if not universe:
        raise ValueError("no market of the signal is in the published panel")
    t = scorecard.wide(cells, truth, universe)
    oi = scorecard.wide(cells, "oi_notional", universe)
    names: list[str] = []
    if baselines is True:
        names = list(SIGNED_BASELINES if signed else UNSIGNED_BASELINES)
        # as in the study: comovement only against coheld_share; dtl shares oi_to_volume_7d's denominator
        names = [n for n in names if not (n == "comovement_30d" and truth != "coheld_share")]
        names = [n for n in names if not (n == "oi_to_volume_7d" and truth == "dtl")]
        # momentum and OI flow define the trend and contrarian cohorts, so their relation is mechanical
        if cohort in STYLE_COHORTS:
            names = [n for n in names if n not in STYLE_DEFINING]
    elif baselines:
        names = list(baselines)
        if cohort in STYLE_COHORTS and set(names) & set(STYLE_DEFINING):
            notes.append(
                "momentum_7d and oi_flow_7d define the trend and contrarian cohorts: their correlation with "
                "these cohorts is mechanical, not evidence"
            )
    frames = {"signal": user.reindex(columns=universe)}
    if names:
        px = proxies if proxies is not None else load_proxies()
        assert isinstance(px, pl.DataFrame)
        missing = [n for n in names if n not in px.columns]
        if missing:
            raise ValueError(f"unknown baselines {missing}")
        frames.update({n: scorecard.wide(px, n, universe) for n in names})
    idx = t.index
    for f in frames.values():
        idx = idx.intersection(f.index)
    mask = t.reindex(idx).notna()
    for f in frames.values():
        mask &= f.reindex(index=idx, columns=t.columns).notna()
    t = t.reindex(idx).where(mask)
    rows = []
    for k, (name, f) in enumerate(frames.items()):
        w = f.reindex(index=idx, columns=t.columns).where(mask)
        sign = expected_sign if name == "signal" else 1
        stats_row = scorecard.score_pair(w, t, oi, pr, signed=signed, expected_sign=sign, seed_offset=k)
        rows.append({"signal": name, **stats_row})
    table = pd.DataFrame(rows)
    table = table[[c for c in COLUMNS if c in table.columns] + [c for c in table.columns if c not in COLUMNS]]
    if int(table.iloc[0]["n_markets"]) == 0:
        notes.append(f"no market has {pr.min_market_days} common days; the within-market statistic is empty")
    return GradeReport(truth, cohort, table, universe, int(mask.to_numpy().sum()), notes=notes)
