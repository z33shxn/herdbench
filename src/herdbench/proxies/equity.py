"""Timing helpers for the stock and futures bridges.

Short interest is a position as of a settlement date; Commitments of Traders is a position as of a
Tuesday. Truth is taken at the first snapshot captured after the matching close, so neither side can see
the other's later data. The settlement-date pairing measures contemporaneous agreement between two
crowds and cannot be traded on in real time; the publication-date pairing is the real-time version.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import polars as pl

NEW_YORK = ZoneInfo("America/New_York")


def us_close_utc(day: date) -> datetime:
    """16:00 New York time on ``day``, in UTC (handles daylight saving)."""
    return datetime.combine(day, time(16, 0), tzinfo=NEW_YORK).astimezone(timezone.utc)


def first_snapshot_after(captures: pl.DataFrame, instant: datetime) -> date | None:
    """Date of the first snapshot captured at or after ``instant``. ``captures``: date, capture (UTC)."""
    hit = captures.filter(pl.col("capture") >= instant).sort("capture")
    return hit["date"][0] if hit.height else None
