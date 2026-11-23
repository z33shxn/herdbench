"""Futures bridges (description only): CFTC positioning against the matching perpetuals, week by week.

Commitments of Traders positions are as of Tuesday; truth is the first snapshot captured from 00:00 UTC
the next day, after the Tuesday close, so the two describe the same positions without either seeing later
data. With about 30 weeks per market these are descriptive correlations, not tests.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd
import polars as pl
from scipy import stats as sps


def truth_date_for_report(report: date, captures: pl.DataFrame) -> date | None:
    start = datetime.combine(report + timedelta(days=1), time(0, 0), tzinfo=timezone.utc)
    hit = captures.filter(pl.col("capture") >= start).sort("capture")
    return hit["date"][0] if hit.height else None


def cot_table(
    truth: pl.DataFrame, cot: pd.DataFrame, captures: pl.DataFrame, metric: str = "ni"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``truth``: date, market, <metric> for one cohort. ``cot``: market, report_date, net_share."""
    lookup = {(r["date"], r["market"]): r[metric] for r in truth.iter_rows(named=True)}
    rows = []
    reports = pd.to_datetime(cot["report_date"]).dt.date
    for market, report, share in zip(cot["market"], reports, cot["net_share"], strict=True):
        d = truth_date_for_report(report, captures)
        if d is None:
            continue
        rows.append(
            {
                "market": market,
                "report_date": report,
                "truth_date": d,
                "truth": lookup.get((d, market)),
                "net_share": share,
            }
        )
    weekly = pd.DataFrame(rows).dropna()
    summary = []
    for m, g in weekly.groupby("market"):
        if len(g) >= 8 and g["truth"].std() > 0 and g["net_share"].std() > 0:
            res = sps.spearmanr(g["truth"], g["net_share"])
            summary.append(
                {"market": m, "n_weeks": len(g), "spearman": float(res.statistic), "p_value": float(res.pvalue)}
            )
        else:
            summary.append({"market": m, "n_weeks": len(g), "spearman": np.nan, "p_value": np.nan})
    return weekly, pd.DataFrame(summary)
