import pytest

from herdbench import universe


@pytest.mark.parametrize(
    ("market", "cls"),
    [
        ("xyz:NVDA", "us_stock"),
        ("xyz:SPCX", "us_stock"),
        ("xyz:GOLD", "commodity"),
        ("xyz:CL", "commodity"),
        ("xyz:DRAM", "etf"),
        ("xyz:TSM", "adr"),
        ("xyz:STRC", "preferred"),
        ("xyz:XYZ100", "index"),
        ("xyz:EUR", "fx"),
        ("xyz:SKHX", "non_us_or_unlisted"),
        ("xyz:PURRDAT", "other"),
        ("BTC", "crypto"),
        ("cash:GOLD", "other"),
    ],
)
def test_classification(market, cls):
    assert universe.classify(market) == cls


def test_no_ticker_is_in_two_classes():
    seen: dict[str, str] = {}
    for cls, tickers in universe._CLASSES.items():
        for t in tickers:
            assert t not in seen, f"{t} is in {seen.get(t)} and {cls}"
            seen[t] = cls
    assert len(universe.US_STOCKS) == 49
    assert universe.stock_markets()[0] == "xyz:AAPL"
