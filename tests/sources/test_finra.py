from datetime import date
from pathlib import Path

import pytest

from herdbench._http import HttpClient
from herdbench.sources import finra
from tests.utils.server import Routes, serve

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_parse_short_interest_reads_the_pipe_file():
    df = finra.parse_short_interest((FIXTURES / "finra_shrt20260130.csv").read_text())
    nvda = df.set_index("symbol").loc["NVDA"]
    assert nvda["settlement_date"] == date(2026, 1, 30)
    assert nvda["short_interest"] == 257091247
    assert nvda["previous_short_interest"] == 261838673
    assert nvda["avg_daily_volume"] == 164060286
    assert nvda["days_to_cover"] == pytest.approx(1.57)
    assert nvda["change_pct"] == pytest.approx(-1.81)
    assert nvda["market_class"] == "NNM"


def test_short_interest_url_uses_the_settlement_date():
    assert finra.short_interest_url(date(2026, 1, 30)) == (
        "https://cdn.finra.org/equity/otcmarket/biweekly/shrt20260130.csv"
    )


def test_discover_settlement_dates_returns_only_published_files():
    routes = Routes(
        head_ok={
            "/equity/otcmarket/biweekly/shrt20260115.csv",
            "/equity/otcmarket/biweekly/shrt20260130.csv",
            "/equity/otcmarket/biweekly/shrt20260213.csv",
        }
    )
    with serve(routes) as base:
        found = finra.discover_settlement_dates(
            date(2026, 1, 1), date(2026, 1, 31), base=base, http=HttpClient(retries=2, backoff=0.0)
        )
    assert found == [date(2026, 1, 15), date(2026, 1, 30)]
