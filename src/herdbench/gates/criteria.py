"""Gates and kill criteria, each reported as passed or failed with its scope.

* Gate 0 (every result): maker and taker notional balance within 1% on at least 99% of market-days.
* Reconciliation (every result): at least 99% of changed position notional between consecutive
  snapshots equals the net of the fills timestamped between the two captures.
* Kill criterion 1 (reported, not gating): US single-stock perps with at least 183 days between first and
  last archived fill.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from herdbench.gates import reconcile
from herdbench.truth import snapshots

FILL_COLUMNS = ["address", "coin", "asset_class", "side", "size", "timestamp"]


def gate0(volume: pl.DataFrame, max_gap: float, min_share: float) -> dict:
    v = volume.filter((pl.col("volume_ntl") > 0) | (pl.col("maker_ntl") > 0)).with_columns(
        gap=(pl.col("volume_ntl") - pl.col("maker_ntl")).abs() / pl.max_horizontal("volume_ntl", "maker_ntl")
    )
    n = v.height
    share = float(v.select((pl.col("gap") <= max_gap).mean()).item()) if n else float("nan")
    return {
        "market_days": n,
        "share_balanced": share,
        "max_gap": float(v.select(pl.col("gap").max()).item()) if n else float("nan"),
        "threshold": min_share,
        "passed": bool(n and share >= min_share),
    }


def kc1(volume: pl.DataFrame, stocks: Sequence[str], min_days: int, min_stocks: int) -> dict:
    span = (
        volume.filter(pl.col("market").is_in(list(stocks)))
        .group_by("market")
        .agg(first=pl.col("day").min(), last=pl.col("day").max())
        .with_columns(days=(pl.col("last") - pl.col("first")).dt.total_days())
    )
    ok = sorted(span.filter(pl.col("days") >= min_days)["market"].to_list())
    return {
        "count": len(ok),
        "qualifying": ok,
        "threshold": min_stocks,
        "passed": len(ok) >= min_stocks,
        "gating": False,
    }


def reconcile_pairs(
    index: dict[date, Path], fill_files: dict[date, Path], groups: dict[date, set[str]] | None = None
) -> list[dict]:
    """Reconcile every consecutive snapshot pair whose two bracketing fill days are archived."""
    out = []
    cache: dict[date, pl.DataFrame] = {}

    def fills(day: date) -> pl.DataFrame:
        if day not in cache:
            cache.clear()
            cache[day] = pl.read_parquet(fill_files[day], columns=FILL_COLUMNS)
        return cache[day]

    for t in sorted(index):
        t1 = t - timedelta(days=1)
        if t1 not in index or t not in fill_files or t1 not in fill_files:
            continue
        cap1 = datetime.fromtimestamp(snapshots.capture_ms(index[t1]) / 1000, tz=timezone.utc)
        cap2 = datetime.fromtimestamp(snapshots.capture_ms(index[t]) / 1000, tz=timezone.utc)
        prev, cur = snapshots.read_raw(index[t1]), snapshots.read_raw(index[t])
        days = sorted({cap1.date(), cap2.date()} | {d for d in (t1, t) if d in fill_files})
        f = pl.concat([pl.read_parquet(fill_files[d], columns=FILL_COLUMNS) for d in days if d in fill_files])
        marks = snapshots.market_table(cur).select(["market", "mark"])
        prev_marks = snapshots.market_table(prev).select(["market", "mark"])
        marks = pl.concat([marks, prev_marks.join(marks, on="market", how="anti")])
        group = groups.get(t) if groups else None
        r = reconcile.pair(prev, cur, f, cap1, cap2, marks, group=group, detail=True)
        r["date"] = t.isoformat()
        out.append(r)
    return out


def reconciliation_summary(pairs: Iterable[dict], threshold: float) -> dict:
    pairs = list(pairs)
    rec = sum(p["reconciled_weight"] for p in pairs)
    tot = sum(p["total_weight"] for p in pairs)
    grec = sum(p.get("group_reconciled_weight", 0.0) for p in pairs)
    gtot = sum(p.get("group_total_weight", 0.0) for p in pairs)
    share = rec / tot if tot else float("nan")
    return {
        "pairs": len(pairs),
        "reconciled_share": share,
        "market_maker_share": grec / gtot if gtot else float("nan"),
        "rest_share": (rec - grec) / (tot - gtot) if tot - gtot else float("nan"),
        "worst_pair_share": min(
            (p["reconciled_weight"] / p["total_weight"] for p in pairs if p["total_weight"]), default=float("nan")
        ),
        "threshold": threshold,
        "passed": bool(tot and share >= threshold),
        "pairs_below_threshold": [
            {"date": p.get("date"), "reconciled_share": p["reconciled_weight"] / p["total_weight"]}
            for p in sorted(pairs, key=lambda q: q.get("date", ""))
            if p["total_weight"] and p["reconciled_weight"] / p["total_weight"] < threshold
        ],
    }
