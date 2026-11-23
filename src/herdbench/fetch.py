"""Download and cache every input. Each step is idempotent: files already present and valid are reused.

Layout under ``data_dir()``:
  hf/perp_archive/...           verbatim snapshots (SHA-256 checked)
  hf/fills_slim/...             fills reduced to 14 columns, with provenance sidecars
  ironflow/hyperliquid-funding  monthly funding and premium files (pinned revision)
  binance/...                   Vision metrics, 5-minute klines and funding (zip files, as published)
  finra/                        short-interest files and the list of settlement dates found
  cftc/                         Commitments of Traders series per contract
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from herdbench._http import HttpClient
from herdbench.config import PREREG
from herdbench.sources import binance, cftc, finra, hfmirror, ironflow


def months_between(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def write_listing(path: Path, revision: str, files) -> str:
    """The mirror's file list at the pinned revision, in a canonical form (sorted, no timestamp), so its hash
    recorded in results/manifest.json is the same for everyone who lists the same revision."""
    payload = {
        "repo": hfmirror.MIRROR_REPO,
        "revision": revision,
        "files": sorted(({"path": f.path, "sha256": f.sha256, "size": f.size} for f in files), key=lambda f: f["path"]),
    }
    text = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return text


def fetch_mirror(data: Path, workers: int = 2) -> tuple[int, int]:
    m = hfmirror.Mirror(revision=PREREG.mirror_revision)
    files = m.list_files()
    root = Path(data) / "hf"
    write_listing(root / "listing.json", PREREG.mirror_revision, files)
    snaps = hfmirror.fetch_snapshots(m, root, files, workers=workers)
    fills = hfmirror.fetch_fills_slim(m, root, files, workers=workers)
    return len(snaps), len(fills)


def fetch_funding(data: Path, start: date, end: date) -> int:
    src = ironflow.Ironflow(
        "hyperliquid-funding", revision=ironflow.FUNDING_REVISION, cache_dir=Path(data) / "ironflow"
    )
    months = months_between(start, end)
    for ym in months:
        src.month(ym)
    return len(months)


def fetch_binance(data: Path, symbols: Iterable[str], start: date, end: date) -> dict[str, int]:
    v = binance.Vision(cache_dir=Path(data) / "binance")
    counts = {"metrics_days": 0, "missing_days": 0, "kline_months": 0, "funding_months": 0}
    for sym in symbols:
        day = start
        while day <= end:
            if v.metrics(sym, day) is None:
                counts["missing_days"] += 1
            else:
                counts["metrics_days"] += 1
            day += timedelta(days=1)
        for ym in months_between(start, end):
            y, mo = int(ym[:4]), int(ym[5:])
            counts["kline_months"] += v.klines(sym, "5m", y, mo) is not None
            counts["funding_months"] += v.funding(sym, y, mo) is not None
    return counts


def fetch_finra(data: Path, start: date, end: date) -> list[date]:
    root = Path(data) / "finra"
    listing = root / "settlement_dates.json"
    if listing.exists():
        found = [date.fromisoformat(d) for d in json.loads(listing.read_text(encoding="utf-8"))]
    else:
        found = finra.discover_settlement_dates(start, end)
        root.mkdir(parents=True, exist_ok=True)
        listing.write_text(json.dumps([d.isoformat() for d in found]), encoding="utf-8")
    src = finra.Finra(cache_dir=root)
    for d in found:
        src.short_interest(d)
    return found


EXTRA_COT = {
    "cme:BTC": cftc.BridgeSpec("133741", "tff", "Bitcoin, CME"),
    "cme:ETH": cftc.BridgeSpec("146021", "tff", "Ether cash-settled, CME"),
}


def fetch_cftc(data: Path, start: date, end: date) -> int:
    root = Path(data) / "cftc"
    root.mkdir(parents=True, exist_ok=True)
    cot = cftc.COT(http=HttpClient(min_interval=0.3))
    n = 0
    for name, spec in {**cftc.BRIDGE, **EXTRA_COT}.items():
        out = root / f"{spec.code}.parquet"
        if out.exists():
            n += 1
            continue
        df: pd.DataFrame = cot.positions(spec, start.isoformat(), end.isoformat())
        tmp = out.with_name(out.name + ".part")
        df.assign(market=name, code=spec.code, report=spec.report).to_parquet(tmp, index=False)
        tmp.replace(out)
        n += 1
    return n
