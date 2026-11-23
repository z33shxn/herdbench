"""A complete, internally consistent synthetic data directory for running the whole pipeline offline.

Positions change only through fills (each trade has one taker and one maker), so every snapshot reconciles
with the fills between captures and maker notional equals taker notional. Prices follow hourly random walks.
Trend-following wallets buy after up days, contrarian wallets sell, the rest trade at random, and one market
maker provides every quote. Funding, Binance Vision, FINRA and CFTC inputs are written in their real file
formats, and the Binance long/short ratios are built from the synthetic crowd so that cross-venue checks
have something to find.
"""

from __future__ import annotations

import io
import json
import math
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

from herdbench.config import PREREG
from herdbench.grading.transfer import FINRA_PUBLICATION
from herdbench.pipeline import Paths
from tests.utils import synth

UTC = timezone.utc
START = date(2026, 1, 1)
DAYS = 150  # snapshots 2026-01-01 .. 2026-05-30; fills from 2025-12-31
MAIN = ("BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX", "ADA", "LTC", "BNB", "SUI", "ARB")
PANEL = MAIN[:10]
STOCKS = ("xyz:AAPL", "xyz:NVDA", "xyz:TSLA", "xyz:AMD", "xyz:META", "xyz:MSFT")
MARKETS = (*MAIN, *STOCKS, "xyz:GOLD")
N_WALLETS = 60
TREND = range(3, 23)
CONTRARIAN = range(23, 43)
MM = "0x" + "ab" * 20
HLP = PREREG.protocol_addresses[0]

#: Smaller samples and thresholds so the whole pipeline runs in seconds; every code path is unchanged.
PREREG_WORLD = replace(
    PREREG,
    evaluation_start="2026-01-12",
    evaluation_end="2026-05-30",
    min_oi_notional=1.0,
    mm_min_notional=1_000.0,
    mm_turnover_multiple=1.0,
    binance_selection_snapshot="2026-01-01",
    min_market_days=40,
    bootstrap_reps=30,
    rotation_draws=30,
    power_reps=30,
    transfer_start="2026-01-15",
    transfer_end="2026-05-15",
    signed_truths=("ni", "ai"),
    unsigned_truths=("abs_ni", "coheld_share"),
)


def wallet(i: int) -> str:
    return f"0x{i + 1:040x}"


MASTER, SUBS = wallet(0), (wallet(1), wallet(2))


def _zip(path: Path, name: str, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, text)
    path.write_bytes(buf.getvalue())


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def make(root: Path, seed: int = 7) -> Paths:
    """Write the world under ``root`` and return its ``Paths`` (results under ``root/results``)."""
    rng = np.random.default_rng(seed)
    paths = Paths.under(root)
    hf = paths.hf
    price = dict(zip(MARKETS, rng.uniform(5, 500, len(MARKETS)), strict=True))
    price["BTC"], price["ETH"], price["xyz:GOLD"] = 90_000.0, 3_000.0, 4_000.0
    pos: dict[tuple[str, str], float] = {}
    av = {wallet(i): 10_000.0 * (1 + i / 10) for i in range(N_WALLETS)} | {MM: 5e6, HLP: 1e8}
    lev = {wallet(i): 3 + (i % 18) for i in range(N_WALLETS)} | {MM: 2, HLP: 1}
    last_ret = dict.fromkeys(MARKETS, 0.0)
    hours: list[dict] = []  # hourly price and taker flow per market (for funding and Binance klines)
    crowd: dict[tuple[date, str], float] = {}  # account imbalance of the non-MM crowd at each snapshot

    def snapshot(day: date) -> None:
        rows = []
        for (user, m), size in pos.items():
            if abs(size) < 1e-12:
                continue
            mark = price[m]
            d = 1.0 / lev[user]
            rows.append(
                {
                    "user": user,
                    "market": m,
                    "size": size,
                    "notional": abs(size) * mark,
                    "liquidation_price": mark * (1 - d) if size > 0 else mark * (1 + d),
                    "leverage": lev[user],
                    "account_value": av[user],
                }
            )
        synth.snapshot(hf, day, rows)
        for m in MARKETS:
            sides = [np.sign(s) for (u, mm_), s in pos.items() if mm_ == m and u not in (MM, HLP) and s != 0]
            crowd[(day, m)] = float(np.mean(sides)) if sides else 0.0

    def trade_day(day: date) -> None:
        rows = []
        for m in MARKETS:
            for h in range(1, 24):
                t = datetime(day.year, day.month, day.day, h, 30, tzinfo=UTC)
                price[m] *= math.exp(rng.normal(0, 0.006))
                i = int(rng.integers(0, N_WALLETS + 1))
                taker = HLP if i == N_WALLETS else wallet(i)
                if i in TREND:
                    buy = rng.random() < (0.8 if last_ret[m] > 0 else 0.2)
                elif i in CONTRARIAN:
                    buy = rng.random() < (0.2 if last_ret[m] > 0 else 0.8)
                else:
                    buy = rng.random() < 0.5
                size = float(rng.integers(1, 5)) * (10.0 / max(price[m], 1.0) ** 0.5)
                side = "buy" if buy else "sell"
                rows.append(
                    {
                        "coin": m,
                        "price": price[m],
                        "size": size,
                        "address": taker,
                        "crossed": True,
                        "side": side,
                        "timestamp": t,
                    }
                )
                rows.append(
                    {
                        "coin": m,
                        "price": price[m],
                        "size": size,
                        "address": MM,
                        "crossed": False,
                        "side": "sell" if buy else "buy",
                        "timestamp": t,
                    }
                )
                pos[(taker, m)] = pos.get((taker, m), 0.0) + (size if buy else -size)
                pos[(MM, m)] = pos.get((MM, m), 0.0) - (size if buy else -size)
                hours.append({"market": m, "t": t, "price": price[m], "flow": size if buy else -size})
            # one forced liquidation per market every few days: the account closes its whole position as taker
            if rng.random() < 0.3:
                holders = [(u, s) for (u, mm_), s in pos.items() if mm_ == m and u not in (MM, HLP) and abs(s) > 0]
                if holders:
                    u, s = holders[int(rng.integers(0, len(holders)))]
                    t = datetime(day.year, day.month, day.day, 23, 45, tzinfo=UTC)
                    side = "sell" if s > 0 else "buy"
                    rows.append(
                        {
                            "coin": m,
                            "price": price[m],
                            "size": abs(s),
                            "address": u,
                            "crossed": True,
                            "side": side,
                            "timestamp": t,
                            "is_liquidation": True,
                            "liquidation_method": "market",
                            "direction": "Close Long" if s > 0 else "Close Short",
                        }
                    )
                    rows.append(
                        {
                            "coin": m,
                            "price": price[m],
                            "size": abs(s),
                            "address": MM,
                            "crossed": False,
                            "side": "buy" if s > 0 else "sell",
                            "timestamp": t,
                        }
                    )
                    pos[(u, m)] = 0.0
                    pos[(MM, m)] = pos.get((MM, m), 0.0) + s
        synth.fills(hf, day, rows)

    prev = dict(price)
    trade_day(START - timedelta(days=1))
    for k in range(DAYS):
        day = START + timedelta(days=k)
        last_ret = {m: math.log(price[m] / prev[m]) for m in MARKETS}
        prev = dict(price)
        snapshot(day)
        trade_day(day)

    _write_derived(paths)
    _write_funding(paths, pd.DataFrame(hours))
    _write_binance(paths, pd.DataFrame(hours), crowd, rng)
    _write_finra(paths, crowd, rng)
    _write_cftc(paths, crowd, rng)
    return paths


def _write_derived(paths: Paths) -> None:
    paths.derived.mkdir(parents=True, exist_ok=True)
    panel = {
        "selection_snapshot": START.isoformat(),
        "markets": list(PANEL),
        "binance_symbols": {m: f"{m}USDT" for m in PANEL},
        "skipped": [],
    }
    (paths.derived / "primary_panel.json").write_text(json.dumps(panel), encoding="utf-8")
    lines = [
        {"kind": "role", "address": MASTER, "role": "user", "master": None},
        {"kind": "role", "address": MM, "role": "user", "master": None},
    ]
    lines += [{"kind": "role", "address": s, "role": "subAccount", "master": MASTER} for s in SUBS]
    lines += [{"kind": "subs", "address": MASTER, "subs": list(SUBS)}, {"kind": "subs", "address": MM, "subs": []}]
    (paths.derived / "entity_roles.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    targets = [MASTER, *SUBS, MM]
    pl.DataFrame({"address": targets, "mm_candidate": [a == MM for a in targets], "top3": [True] * 4}).write_parquet(
        paths.derived / "entity_map_targets.parquet"
    )


def _write_funding(paths: Paths, hours: pd.DataFrame) -> None:
    rows = []
    for m, g in hours.groupby("market"):
        g = g.sort_values("t")
        flow = g["flow"].rolling(24, min_periods=1).sum().to_numpy()
        for t, f in zip(g["t"], flow, strict=True):
            premium = 0.0004 * math.tanh(f / 20.0)
            funding = 0.0000125 if abs(premium) < 0.00005 else 0.0000125 + 0.125 * premium
            rows.append(
                {
                    "timestamp": t.replace(minute=0),
                    "market": f"{m}-PERP",
                    "coin": m,
                    "dex": "",
                    "funding_rate": funding,
                    "premium": premium,
                }
            )
    df = pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    root = paths.raw / "ironflow" / "hyperliquid-funding"
    root.mkdir(parents=True, exist_ok=True)
    for (ym,), part in df.with_columns(ym=pl.col("timestamp").dt.strftime("%Y-%m")).group_by(["ym"]):
        part.drop("ym").write_parquet(root / f"{ym}.parquet")


def _write_binance(paths: Paths, hours: pd.DataFrame, crowd: dict, rng: np.random.Generator) -> None:
    root = paths.raw / "binance" / "futures" / "um"
    for m in PANEL:
        sym = f"{m}USDT"
        for k in range(-1, DAYS):
            day = START + timedelta(days=k)
            ai = crowd.get((day, m), crowd.get((START, m), 0.0))
            lines = [
                "create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
                "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio"
            ]
            for j in range(48):
                t = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(minutes=30 * j)
                g = math.exp(1.5 * ai + rng.normal(0, 0.1))
                lines.append(
                    f"{t:%Y-%m-%d %H:%M:%S},{sym},{1000 + 100 * ai + j:.4f},{1e6:.4f},{g * 1.1:.6f},"
                    f"{g * 0.9:.6f},{g:.6f},{rng.uniform(0.8, 1.2):.6f}"
                )
            _zip(
                root / "daily" / "metrics" / sym / f"{sym}-metrics-{day:%Y-%m-%d}.zip",
                f"{sym}-metrics-{day:%Y-%m-%d}.csv",
                "\n".join(lines) + "\n",
            )
        h = hours[hours["market"] == m].copy()
        h["ym"] = h["t"].map(lambda t: f"{t:%Y-%m}")
        for ym, g in h.groupby("ym"):
            kl = [
                "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,"
                "taker_buy_quote_volume,ignore"
            ]
            for t, p, f in zip(g["t"], g["price"], g["flow"], strict=True):
                start = t.replace(minute=0)
                vol = 10.0 + abs(f)
                buy = vol / 2 + f / 2
                kl.append(f"{_ms(start)},{p},{p},{p},{p},{vol},{_ms(start) + 3_599_999},{vol * p},10,{buy},{buy * p},0")
            _zip(
                root / "monthly" / "klines" / sym / "5m" / f"{sym}-5m-{ym}.zip",
                f"{sym}-5m-{ym}.csv",
                "\n".join(kl) + "\n",
            )
            first = datetime.strptime(ym + "-01", "%Y-%m-%d").replace(tzinfo=UTC)
            fr = ["calc_time,funding_interval_hours,last_funding_rate"]
            t = first
            while t.strftime("%Y-%m") == ym:
                fr.append(f"{_ms(t)},8,{rng.normal(0.0001, 0.00005):.8f}")
                t += timedelta(hours=8)
            _zip(
                root / "monthly" / "fundingRate" / sym / f"{sym}-fundingRate-{ym}.zip",
                f"{sym}-fundingRate-{ym}.csv",
                "\n".join(fr) + "\n",
            )


def _write_finra(paths: Paths, crowd: dict, rng: np.random.Generator) -> None:
    root = paths.raw / "finra"
    root.mkdir(parents=True, exist_ok=True)
    header = (
        "accountingYearMonthNumber|symbolCode|issueName|issuerServicesGroupExchangeCode|marketClassCode|"
        "currentShortPositionQuantity|previousShortPositionQuantity|stockSplitFlag|averageDailyVolumeQuantity|"
        "daysToCoverQuantity|revisionFlag|changePercent|changePreviousNumber|settlementDate"
    )
    lo, hi = date.fromisoformat(PREREG_WORLD.transfer_start), date.fromisoformat(PREREG_WORLD.transfer_end)
    for s in sorted(d for d in FINRA_PUBLICATION if lo <= d <= hi):
        lines = [header]
        for m in (*STOCKS, "xyz:DECOY"):
            tick = m.split(":")[1]
            ai = crowd.get((s, m), 0.0)
            cur = int(1e7 * math.exp(-1.0 * ai + rng.normal(0, 0.2)))
            prev = int(cur * math.exp(rng.normal(0, 0.1)))
            change = (cur - prev) / prev * 100
            lines.append(
                f"{s:%Y%m%d}|{tick}|{tick} Inc. Common Stock|R|NNM|{cur}|{prev}||{2_000_000}|"
                f"{cur / 2e6:.2f}||{change:.2f}|{cur - prev}|{s:%Y-%m-%d}"
            )
        (root / f"shrt{s:%Y%m%d}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_cftc(paths: Paths, crowd: dict, rng: np.random.Generator) -> None:
    root = paths.raw / "cftc"
    root.mkdir(parents=True, exist_ok=True)
    tuesdays = [START + timedelta(days=k) for k in range(DAYS - 2) if (START + timedelta(days=k)).weekday() == 1]
    for code, market, report in (("088691", "xyz:GOLD", "disaggregated"), ("133741", "cme:BTC", "tff")):
        perp = "BTC" if market == "cme:BTC" else market
        rows = []
        for t in tuesdays:
            share = 0.5 * crowd.get((t + timedelta(days=1), perp), 0.0) + rng.normal(0, 0.05)
            oi = 100_000
            long_ = int(oi * (0.3 + share / 2))
            short = int(oi * 0.3)
            rows.append(
                {
                    "report_date": pd.Timestamp(t),
                    "open_interest": oi,
                    "long": long_,
                    "short": short,
                    "net_share": (long_ - short) / oi,
                }
            )
        df = pd.DataFrame(rows)
        if report == "tff":
            df["asset_net_share"] = df["net_share"] * 0.5 + rng.normal(0, 0.02, len(df))
        df.assign(market=market, code=code, report=report).to_parquet(root / f"{code}.parquet", index=False)
